"""Coverage contracts for continuous improvement, Git, and wiki boundaries."""
# ruff: noqa: F405

from tests.coverage_cases._runtime_support import *  # noqa: F403


def test_artifact_resolve_rejects_directory_even_when_index_lists_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    directory = store.root / "T-1" / artifacts.FILES_DIR / "folder"
    directory.mkdir(parents=True)
    record = artifacts.ArtifactRecord(
        name="folder",
        title="folder",
        summary="",
        content_type="application/octet-stream",
        byte_size=0,
        sha256="",
        collected_at="",
    )
    monkeypatch.setattr(store, "list_for", lambda _identifier: [record])
    assert store.resolve_file("T-1", "folder") is None


def test_artifact_resolve_rejects_index_path_outside_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    record = artifacts.ArtifactRecord(
        name="outside.txt",
        title="outside",
        summary="",
        content_type="text/plain",
        byte_size=6,
        sha256="",
        collected_at="",
    )
    monkeypatch.setattr(store, "list_for", lambda _identifier: [record])
    monkeypatch.setattr(store, "_files_dir", lambda _identifier: tmp_path)
    assert store.resolve_file("T-1", "outside.txt") is None


def test_artifact_collect_skips_hidden_and_empty_source_sets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    magic = workspace / artifacts.DEFAULT_MAGIC_DIR
    magic.mkdir(parents=True)
    (magic / ".hidden").write_text("secret", encoding="utf-8")
    store = artifacts.ArtifactStore(tmp_path / "store")
    result = store.collect_from_workspace(workspace, identifier="T-1")
    assert result.collected == []
    assert result.skipped == [(".hidden", "hidden")]


def test_artifact_collect_skips_simulated_symlink_without_following_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    magic = workspace / artifacts.DEFAULT_MAGIC_DIR
    magic.mkdir(parents=True)
    candidate = magic / "link.txt"
    candidate.write_text("payload", encoding="utf-8")
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == candidate or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    result = artifacts.ArtifactStore(tmp_path / "store").collect_from_workspace(
        workspace, identifier="T-1"
    )
    assert result.skipped == [("link.txt", "symlink")]


def test_artifact_collect_classifies_stat_hash_and_copy_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    magic = workspace / artifacts.DEFAULT_MAGIC_DIR
    magic.mkdir(parents=True)
    stat_bad = magic / "stat-bad.txt"
    hash_bad = magic / "hash-bad.txt"
    copy_bad = magic / "copy-bad.txt"
    for path in (stat_bad, hash_bad, copy_bad):
        path.write_text("payload", encoding="utf-8")
    original_stat = Path.stat
    original_is_file = Path.is_file
    original_sha = artifacts._sha256_file

    def stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == stat_bad:
            raise OSError("stat denied")
        return original_stat(path, *args, **kwargs)

    def digest(path: Path) -> str:
        if path == hash_bad:
            raise OSError("read denied")
        return original_sha(path)

    store = artifacts.ArtifactStore(tmp_path / "store")
    real_copy = store._copy_into_store

    def copy(identifier: str, source: Path, name: str) -> int:
        if source == copy_bad:
            raise OSError("copy denied")
        return real_copy(identifier, source, name)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: (
            True if path in {stat_bad, hash_bad, copy_bad} else original_is_file(path)
        ),
    )
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(artifacts, "_sha256_file", digest)
    monkeypatch.setattr(store, "_copy_into_store", copy)
    result = store.collect_from_workspace(workspace, identifier="T-1")
    assert sorted(result.skipped) == [
        ("copy-bad.txt", "copy_failed"),
        ("hash-bad.txt", "unreadable"),
        ("stat-bad.txt", "unreadable"),
    ]


def test_artifact_sweep_handles_missing_root_noise_and_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    assert store.sweep(known_identifiers={"LIVE"}, ttl_days=1) == []

    store.root.mkdir()
    (store.root / "plain-file").write_text("x", encoding="utf-8")
    (store.root / ".invalid").mkdir()
    (store.root / "OLD-1").mkdir()
    monkeypatch.setattr(store, "_newest_mtime", lambda _path: 0.0)
    monkeypatch.setattr(
        artifacts.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("delete denied")),
    )
    assert store.sweep(known_identifiers={"LIVE"}, ttl_days=1) == []


def test_artifact_unique_name_reports_exhaustion() -> None:
    taken = {"name.txt", *(f"name-{counter}.txt" for counter in range(2, 1000))}
    with pytest.raises(OSError, match="could not find a free artifact name"):
        artifacts.ArtifactStore._unique_name("name.txt", taken)


def test_artifact_copy_cleanup_and_discard_tolerate_filesystem_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store = artifacts.ArtifactStore(tmp_path / "store")
    monkeypatch.setattr(
        artifacts.shutil,
        "copyfile",
        lambda *_args: (_ for _ in ()).throw(OSError("copy failed")),
    )
    monkeypatch.setattr(
        artifacts.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    with pytest.raises(OSError, match="copy failed"):
        store._copy_into_store("T-1", source, "target.txt")
    store._discard_from_store("T-1", "missing.txt")


def test_artifact_index_loader_skips_nonmaps_and_invalid_records(
    tmp_path: Path,
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    index = store.root / "T-1" / artifacts.INDEX_NAME
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            {
                "entries": [
                    "not-a-map",
                    {},
                    {"name": "bad-size", "byte_size": "not-an-int"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.list_for("T-1") == []

    index.write_text('{"entries": "not-a-list"}', encoding="utf-8")
    assert store.list_for("T-1") == []


def test_artifact_rebuild_skips_directory_symlink_and_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    files = store.root / "T-1" / artifacts.FILES_DIR
    files.mkdir(parents=True)
    directory = files / "directory"
    directory.mkdir()
    link_like = files / "link.txt"
    link_like.write_text("link", encoding="utf-8")
    unreadable = files / "unreadable.txt"
    unreadable.write_text("bad", encoding="utf-8")
    good = files / "good.txt"
    good.write_text("good", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    original_sha = artifacts._sha256_file

    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == link_like or original_is_symlink(path),
    )

    def digest(path: Path) -> str:
        if path == unreadable:
            raise OSError("cannot read")
        return original_sha(path)

    monkeypatch.setattr(artifacts, "_sha256_file", digest)
    records = store._rebuild_index_from_files("T-1", log_missing=True)
    assert [record.name for record in records] == ["good.txt"]


def test_artifact_index_write_cleanup_tolerates_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = artifacts.ArtifactStore(tmp_path / "store")
    monkeypatch.setattr(
        artifacts.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        artifacts.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("unlink failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        store._write_index("T-1", [])


def test_artifact_newest_mtime_skips_unstatable_directory_and_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ticket"
    directory.mkdir()
    child = directory / "file.txt"
    child.write_text("x", encoding="utf-8")
    original_stat = Path.stat

    def stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path in {directory, child}:
            raise OSError("stat denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert artifacts.ArtifactStore._newest_mtime(directory) == 0.0


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("{% if %}{% endif %}", "empty if condition"),
        ("{% if true %}{% elsif %}{% endif %}", "empty elsif condition"),
        ("{% if true %}body", "missing end tag"),
        ("{% for broken %}{% endfor %}", "malformed for tag"),
        ("{% for item in items %}body", "missing end tag"),
        ("{% raw %}body", "unterminated raw"),
        ("{% unknown %}", "unknown tag"),
    ],
)
def test_prompt_parser_reports_malformed_control_structures(
    template: str, message: str
) -> None:
    with pytest.raises(prompt.TemplateParseError, match=message):
        prompt.render(template, {"items": []})


def test_prompt_raw_block_preserves_liquid_syntax_verbatim() -> None:
    template = "before {% raw %}{{ value }}{% if true %}x{% endif %}{% endraw %} after"
    assert prompt.render(template, {"value": "changed"}) == (
        "before {{value}}{%if true%}x{%endif%} after"
    )


def test_prompt_path_resolution_handles_missing_invalid_object_and_index_access() -> (
    None
):
    assert prompt._resolve_path("", {}) is prompt._MISSING
    with pytest.raises(prompt.TemplateRenderError, match="invalid variable name"):
        prompt._resolve_path("1bad", {})
    with pytest.raises(prompt.TemplateRenderError, match="invalid attribute access"):
        prompt._resolve_path("item.!", {"item": {}})
    with pytest.raises(prompt.TemplateRenderError, match="invalid index access"):
        prompt._resolve_path("items[bad]", {"items": []})
    with pytest.raises(prompt.TemplateRenderError, match="invalid path tail"):
        prompt._resolve_path("item$", {"item": {}})

    assert prompt._resolve_path("item.missing", {"item": {}}) is prompt._MISSING
    assert (
        prompt._resolve_path("item.missing", {"item": SimpleNamespace()})
        is prompt._MISSING
    )
    assert prompt._resolve_path("items[9]", {"items": []}) is prompt._MISSING
    assert prompt._resolve_path("items[0].name", {"items": [{"name": "first"}]}) == (
        "first"
    )


def test_prompt_literal_and_expression_evaluation_boundaries() -> None:
    env: dict[str, Any] = {}
    assert prompt._eval_value("-7", env) == -7
    assert prompt._eval_value("true", env) is True
    assert prompt._eval_value("false", env) is False
    assert prompt._eval_value("nil", env) is None
    with pytest.raises(prompt.TemplateRenderError, match="invalid expression"):
        prompt._eval_value("1 + 2", env)
    with pytest.raises(prompt.TemplateRenderError, match="empty filter segment"):
        prompt._eval_output_expr("value | ", {"value": "x"})


def test_prompt_pipeline_and_filter_argument_split_preserve_escaped_quotes() -> None:
    assert prompt._split_pipeline('value | default: "a\\"|b" | strip') == [
        "value",
        'default: "a\\"|b"',
        "strip",
    ]
    assert prompt._split_args('"a\\",b", 2') == ['"a\\",b"', "2"]


def test_prompt_condition_negation_and_truthiness() -> None:
    assert prompt._eval_condition("!enabled", {"enabled": False}) is True
    assert prompt._truthy(None) is False
    assert prompt._truthy(False) is False
    assert prompt._truthy(0) is False
    assert prompt._truthy("") is False
    assert prompt._truthy("value") is True


def test_prompt_elsif_branch_and_object_attribute_resolution() -> None:
    template = "{% if first %}a{% elsif second %}b{% else %}c{% endif %}"
    assert prompt.render(template, {"first": False, "second": True}) == "b"
    assert (
        prompt._resolve_path("item.name", {"item": SimpleNamespace(name="object-name")})
        == "object-name"
    )


def test_prompt_for_loop_supports_none_and_mapping_and_rejects_scalars() -> None:
    assert prompt.render(
        "A{% for x in values %}{{ x }}{% endfor %}B", {"values": None}
    ) == ("AB")
    rendered = prompt.render(
        "{% for pair in values %}{{ pair[0] }}={{ pair[1] }};{% endfor %}",
        {"values": {"a": 1, "b": 2}},
    )
    assert rendered == "a=1;b=2;"
    with pytest.raises(prompt.TemplateRenderError, match="not iterable"):
        prompt.render("{% for x in values %}{{ x }}{% endfor %}", {"values": 3})


def test_prompt_env_accepts_plain_mapping_issue() -> None:
    issue = {"identifier": "T-1", "state": "Todo", "description": "body"}
    env = prompt.build_prompt_env(issue, attempt=1)
    assert env["issue"]["identifier"] == "T-1"
    assert env["attempt"] == 1

    rendered, _ = prompt.build_first_turn_prompt(
        prompt_template="{{ issue.identifier }}",
        issue=issue,
        attempt=None,
        language="en",
        max_turns=3,
        extra_context="  literal {{ skill_payload }}  ",
    )
    assert rendered.endswith("T-1\n\nliteral {{ skill_payload }}\n")


def test_ci_stream_reader_handles_absent_and_truncated_streams() -> None:
    assert asyncio.run(ci._read_stream(None, 3)) == ("", False)

    class Stream:
        def __init__(self) -> None:
            self.chunks = [b"abcd", b"ef", b""]

        async def read(self, _size: int) -> bytes:
            return self.chunks.pop(0)

    assert asyncio.run(ci._read_stream(Stream(), 3)) == ("abc", True)


def test_ci_run_argv_reports_missing_command(tmp_path: Path) -> None:
    async def missing_factory(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError

    execution = asyncio.run(
        ci.run_argv(
            ("missing-tool", "--version"),
            tmp_path,
            timeout_s=1,
            proc_factory=missing_factory,
        )
    )
    assert execution.missing is True
    assert execution.output == "command not found: missing-tool"


def test_ci_output_summary_and_optional_missing_check(tmp_path: Path) -> None:
    assert ci._first_output_line("\n  \n") == ""
    execution = _ci_execution(("tool",), returncode=7)
    assert ci._failed_check_summary("tool", execution) == "tool exited 7"

    async def missing(*_args: Any, **_kwargs: Any) -> ci.CommandExecution:
        return _ci_execution(("tool",), returncode=None, output="missing", missing=True)

    optional = ci.CheckSpec("optional", ("tool",), optional=True)
    required = ci.CheckSpec("required", ("tool",), optional=False)
    assert (
        asyncio.run(
            ci.run_predefined_check(optional, tmp_path, run_argv_func=missing)
        ).status
        == "not_available"
    )
    assert (
        asyncio.run(
            ci.run_predefined_check(required, tmp_path, run_argv_func=missing)
        ).status
        == "not_proven"
    )


def test_ci_baseline_fails_closed_for_branch_and_sha_errors(tmp_path: Path) -> None:
    branch_cmd = ("git", "rev-parse", "--abbrev-ref", "HEAD")
    sha_cmd = ("git", "rev-parse", "HEAD")
    branch_fail = _ci_runner(
        {branch_cmd: _ci_execution(branch_cmd, returncode=1, output="no branch")}
    )
    branch, sha = asyncio.run(
        ci._git_branch_and_sha(tmp_path, run_argv_func=branch_fail)
    )
    assert branch.returncode == 1 and sha.missing is True
    proof = asyncio.run(ci.prove_baseline(tmp_path, run_argv_func=branch_fail))
    assert proof.status == "not_proven" and proof.summary == "no branch"

    sha_fail = _ci_runner(
        {
            branch_cmd: _ci_execution(branch_cmd, output="dev"),
            sha_cmd: _ci_execution(sha_cmd, returncode=1, output="no sha"),
        }
    )
    proof = asyncio.run(ci.prove_baseline(tmp_path, run_argv_func=sha_fail))
    assert proof.branch == "dev" and proof.sha is None and proof.summary == "no sha"


def test_ci_baseline_reports_target_status_and_upstream_failures(
    tmp_path: Path,
) -> None:
    branch_cmd = ("git", "rev-parse", "--abbrev-ref", "HEAD")
    sha_cmd = ("git", "rev-parse", "HEAD")
    target_cmd = ("git", "rev-parse", "--verify", "main")
    status_cmd = ("git", "status", "--porcelain")
    upstream_cmd = (
        "git",
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
    )
    ahead_cmd = ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}")
    base = {
        branch_cmd: _ci_execution(branch_cmd, output="main"),
        sha_cmd: _ci_execution(sha_cmd, output="a" * 40),
    }

    proof = asyncio.run(
        ci.prove_baseline(
            tmp_path,
            target_branch="main",
            run_argv_func=_ci_runner(
                {**base, target_cmd: _ci_execution(target_cmd, returncode=1)}
            ),
        )
    )
    assert "cannot be resolved" in proof.summary

    proof = asyncio.run(
        ci.prove_baseline(
            tmp_path,
            run_argv_func=_ci_runner(
                {
                    **base,
                    status_cmd: _ci_execution(
                        status_cmd, returncode=1, output="bad status"
                    ),
                }
            ),
        )
    )
    assert proof.summary == "bad status"

    proof = asyncio.run(
        ci.prove_baseline(
            tmp_path,
            run_argv_func=_ci_runner(
                {
                    **base,
                    status_cmd: _ci_execution(status_cmd),
                    upstream_cmd: _ci_execution(upstream_cmd, output="origin/main"),
                    ahead_cmd: _ci_execution(ahead_cmd, returncode=1),
                }
            ),
        )
    )
    assert proof.upstream == "origin/main"
    assert proof.summary == "upstream configured but not reachable"

    proof = asyncio.run(
        ci.prove_baseline(
            tmp_path,
            run_argv_func=_ci_runner(
                {
                    **base,
                    status_cmd: _ci_execution(status_cmd),
                    upstream_cmd: _ci_execution(upstream_cmd, output="origin/main"),
                    ahead_cmd: _ci_execution(ahead_cmd, output="0\t0"),
                }
            ),
        )
    )
    assert proof.status == "passed" and proof.upstream == "origin/main (0\t0)"


def test_ci_prepare_baseline_reports_target_and_worktree_failures(
    tmp_path: Path,
) -> None:
    branch_cmd = ("git", "rev-parse", "--abbrev-ref", "HEAD")
    sha_cmd = ("git", "rev-parse", "HEAD")
    target_cmd = ("git", "rev-parse", "--verify", "main")
    base = {
        branch_cmd: _ci_execution(branch_cmd, output="dev"),
        sha_cmd: _ci_execution(sha_cmd, output="a" * 40),
    }
    prepared = asyncio.run(
        ci._prepare_baseline(
            tmp_path,
            "main",
            run_argv_func=_ci_runner(
                {**base, target_cmd: _ci_execution(target_cmd, returncode=1)}
            ),
        )
    )
    assert "cannot be resolved" in prepared.proof.summary

    async def worktree_fail(
        argv: tuple[str, ...], _cwd: Path, *, timeout_s: float
    ) -> ci.CommandExecution:
        del timeout_s
        if argv in base:
            return base[argv]
        if argv == target_cmd:
            return _ci_execution(argv, output="b" * 40)
        assert argv[:3] == ("git", "worktree", "add")
        return _ci_execution(argv, returncode=1, output="cannot add")

    prepared = asyncio.run(
        ci._prepare_baseline(tmp_path, "main", run_argv_func=worktree_fail)
    )
    assert "could not create temporary worktree" in prepared.proof.summary


def test_ci_prepare_baseline_reports_branch_and_sha_failures(tmp_path: Path) -> None:
    branch_cmd = ("git", "rev-parse", "--abbrev-ref", "HEAD")
    sha_cmd = ("git", "rev-parse", "HEAD")
    prepared = asyncio.run(
        ci._prepare_baseline(
            tmp_path,
            "main",
            run_argv_func=_ci_runner(
                {
                    branch_cmd: _ci_execution(
                        branch_cmd, returncode=1, output="no branch"
                    )
                }
            ),
        )
    )
    assert prepared.proof.branch is None and prepared.proof.summary == "no branch"

    prepared = asyncio.run(
        ci._prepare_baseline(
            tmp_path,
            "main",
            run_argv_func=_ci_runner(
                {
                    branch_cmd: _ci_execution(branch_cmd, output="dev"),
                    sha_cmd: _ci_execution(sha_cmd, returncode=1, output="no sha"),
                }
            ),
        )
    )
    assert prepared.proof.branch == "dev" and prepared.proof.summary == "no sha"


def test_ci_mode_state_tracker_and_registration_unsupported_paths(
    tmp_path: Path,
) -> None:
    state_path = ci.mode_state_path(tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("[]", encoding="utf-8")
    assert ci.load_mode_state(tmp_path) == {}

    cfg = SimpleNamespace(
        tracker=SimpleNamespace(kind="linear", board_root=None),
        continuous_improvement=SimpleNamespace(),
    )
    assert ci._tracker_or_none(cfg) is None
    assert (
        ci.register_proposals(cfg, (), request="REQ") == ci.TicketRegistrationResult()
    )
    proposal = ci.ImprovementProposal(mode="readiness", title="Title", goal="Goal")
    result = ci.register_proposals(cfg, (proposal,), request="REQ")
    assert result.unsupported_tracker is True


def test_ci_registration_deduplicates_existing_marker_without_board_write() -> None:
    proposal = ci.ImprovementProposal(mode="readiness", title="Title", goal="Goal")
    existing = SimpleNamespace(
        title="Different title",
        description=f"body\n{proposal.marker}\n",
    )
    board = SimpleNamespace(
        create_with_next_identifier=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate must not create a ticket")
        )
    )
    cfg = SimpleNamespace(
        continuous_improvement=SimpleNamespace(max_improvement_tickets_per_run=3),
        tracker=SimpleNamespace(active_states=("Todo",)),
    )
    result = ci.register_proposals(
        cfg,
        (proposal,),
        request="REQ",
        tracker=board,
        existing=[existing],
    )
    assert result.duplicates == 1 and result.tickets_created == 0


def test_ci_link_blocker_and_root_note_noop_boundaries() -> None:
    tracker = SimpleNamespace(fetch_issue_full_by_id=lambda _source: None)
    ci._link_blocker(tracker, source="T-1", fix="T-1")
    ci._link_blocker(tracker, source="T-1", fix="FIX-1")
    already_linked = SimpleNamespace(
        blocked_by=(SimpleNamespace(identifier="FIX-1", id="id-fix"),)
    )
    tracker = SimpleNamespace(
        fetch_issue_full_by_id=lambda _source: already_linked,
        update_fields=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing blocker must not be rewritten")
        ),
    )
    ci._link_blocker(tracker, source="T-1", fix="FIX-1")
    issue = SimpleNamespace(description=None)
    assert ci._root_cause_note(issue) == "(no blocker section on the source ticket)"


def test_ci_unsupported_tracker_modes_and_default_reopen_state() -> None:
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(kind="linear", board_root=None, active_states=()),
    )
    assert ci._blocked_source_reopen_state(cfg) == "Todo"
    assert ci.reopen_resolved_blocked_sources(cfg) == ((), "unsupported tracker")
    assert ci.collect_blocked_fix_proposals(cfg) == ((), "unsupported tracker")


def test_ci_app_context_tolerates_unreadable_readme_and_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("readme", encoding="utf-8")
    wiki = tmp_path / "docs" / "llm-wiki" / "INDEX.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text("wiki", encoding="utf-8")
    original_read = Path.read_text

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path in {readme, wiki}:
            raise OSError("unreadable")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert ci.build_app_context(tmp_path, []) == "Open board tickets\n(none)"


def test_ci_agent_proposal_parser_handles_bad_container_priority_and_reply_json(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output.json"
    output.write_text('{"proposals": "bad"}', encoding="utf-8")
    assert ci.parse_agent_proposals("readiness", output_path=output, reply="") == ()

    output.write_text(
        json.dumps(
            {"proposals": [{"title": "Title", "goal": "Goal", "priority": True}]}
        ),
        encoding="utf-8",
    )
    proposals = ci.parse_agent_proposals("readiness", output_path=output, reply="")
    assert proposals[0].priority == 2

    output.unlink()
    assert ci._load_json_object(output, "no json here") == {}
    assert ci._load_json_object(output, "prefix {bad json} suffix") == {}


def test_ci_agent_mode_and_outcome_status_empty_paths(tmp_path: Path) -> None:
    cfg = SimpleNamespace()

    async def unused_runner(_task: Any) -> str:
        raise AssertionError("runner must not be called")

    proposals, summary = asyncio.run(
        ci.run_agent_mode(cfg, tmp_path, "unsupported", unused_runner)
    )
    assert proposals == () and summary == "no prompt template for unsupported"
    assert ci._check_outcome("security", ()).status == "not_available"
    baseline = ci.BaselineProof("not_proven", None, None, False, None, "bad")
    assert ci._run_status(baseline, (), ()) == "not_proven"


def test_ci_run_records_due_check_modes_when_baseline_cannot_be_proven(
    tmp_path: Path,
) -> None:
    from tests.test_continuous_improvement import _modes_workflow

    cfg = _modes_workflow(tmp_path, "readiness, security")

    async def branch_failure(
        argv: tuple[str, ...], _cwd: Path, **_kwargs: Any
    ) -> ci.CommandExecution:
        return _ci_execution(tuple(argv), returncode=1, output="baseline unavailable")

    result = asyncio.run(
        ci.run_continuous_improvement(
            cfg,
            tmp_path,
            lambda _phase: None,
            run_argv_func=branch_failure,
            clock=lambda: 100.0,
        )
    )
    assert [(outcome.mode, outcome.status) for outcome in result.modes] == [
        ("readiness", "not_proven"),
        ("security", "not_proven"),
    ]


def test_ci_run_propagates_agent_cancellation_and_marks_unsupported_proposal_tracker(
    tmp_path: Path,
) -> None:
    from tests.test_continuous_improvement import _modes_workflow

    cfg = _modes_workflow(tmp_path, "feature_improvements")

    async def cancelled(_task: ci.AgentTask) -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ci.run_continuous_improvement(
                cfg,
                tmp_path,
                lambda _phase: None,
                agent_runner=cancelled,
                clock=lambda: 100.0,
            )
        )

    cfg = dataclasses.replace(
        cfg,
        tracker=dataclasses.replace(cfg.tracker, kind="linear", board_root=None),
    )

    async def propose(task: ci.AgentTask) -> str:
        task.output_path.write_text(
            json.dumps({"proposals": [{"title": "Improve", "goal": "Ship it"}]}),
            encoding="utf-8",
        )
        return "proposal written"

    result = asyncio.run(
        ci.run_continuous_improvement(
            cfg,
            tmp_path,
            lambda _phase: None,
            agent_runner=propose,
            clock=lambda: 100.0,
        )
    )
    assert result.tickets_created == 0


def test_ci_file_lease_write_corruption_refresh_and_release_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lease.json"
    lease = ci.FileLease(path, ttl_seconds=10, now=lambda: 100.0)
    lease._write()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["token"] == lease._token

    path.write_text("bad json", encoding="utf-8")
    assert lease._owns_current_file() is False
    assert lease._is_stale() is True
    lease.refresh()

    lease._held = True
    lease.refresh()
    assert lease._held is False
    lease.release()

    lease._held = True
    path.write_text(json.dumps(lease._payload()), encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    lease.release()
    assert lease._held is False

    lease._held = True
    path.write_text(json.dumps(lease._payload()), encoding="utf-8")
    writes: list[bool] = []
    monkeypatch.setattr(lease, "_write", lambda: writes.append(True))
    lease.refresh()
    assert writes == [True]


def test_ci_file_lease_stale_race_exhausts_two_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lease.json"
    path.write_text('{"acquired_at": 0}', encoding="utf-8")
    lease = ci.FileLease(path, ttl_seconds=1, now=lambda: 100.0)
    monkeypatch.setattr(
        ci.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
    )
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert lease.acquire() is False
    lease._held = True
    assert lease.acquire() is True


def test_ci_run_argv_cancellation_kills_process_and_cancels_stream_tasks(
    tmp_path: Path,
) -> None:
    class NeverEndingStream:
        async def read(self, _size: int) -> bytes:
            await asyncio.Event().wait()
            return b""

    proc = SimpleNamespace(
        stdout=NeverEndingStream(),
        stderr=NeverEndingStream(),
        killed=False,
    )
    proc.kill = lambda: setattr(proc, "killed", True)

    async def factory(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    calls = 0

    async def wait(_proc: Any, *, timeout: float) -> int | None:
        nonlocal calls
        del timeout
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return 0

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ci.run_argv(
                ("tool",),
                tmp_path,
                timeout_s=1,
                proc_factory=factory,
                proc_wait=wait,
            )
        )
    assert proc.killed is True and calls == 2


@pytest.mark.parametrize(
    "error", [OSError("missing"), subprocess.TimeoutExpired("git", 1)]
)
def test_git_ops_run_collapses_spawn_and_timeout_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(
        git_ops.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    assert git_ops._run(tmp_path, ["git", "status"], 1) is None


def test_git_ops_list_and_delete_failure_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_ops, "_run", lambda *_args, **_kwargs: None)
    assert git_ops.list_remotes(tmp_path) == []
    assert git_ops.delete_branch(tmp_path, "topic").status == "git_failed"

    monkeypatch.setattr(
        git_ops, "_run", lambda *_args, **_kwargs: _completed(1, stderr="fatal: other")
    )
    assert git_ops.delete_branch(tmp_path, "topic").status == "git_failed"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, "timeout"),
        (_completed(1, stderr="Authentication failed"), "auth_failed"),
        (_completed(1, stderr="fatal: other"), "git_failed"),
    ],
)
def test_git_ops_push_classifies_transport_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str] | None,
    expected: str,
) -> None:
    monkeypatch.setattr(git_ops, "_run", lambda *_args, **_kwargs: result)
    assert git_ops.push_branch(tmp_path, "topic", "origin").status == expected


@pytest.mark.parametrize(
    ("result", "expected", "expected_url"),
    [
        (None, "timeout", ""),
        (
            _completed(
                1, stderr="a pull request already exists https://example/pull/1"
            ),
            "pr_exists",
            "https://example/pull/1",
        ),
        (
            _completed(
                1, stderr="none of the git remotes point to a known GitHub host"
            ),
            "not_a_github_remote",
            "",
        ),
        (
            _completed(1, stderr="auth required; run gh auth login"),
            "gh_auth_failed",
            "",
        ),
        (_completed(1, stderr="unknown failure"), "gh_failed", ""),
        (
            _completed(0, stdout="https://example/pull/2\n"),
            "created",
            "https://example/pull/2",
        ),
    ],
)
def test_git_ops_pull_request_classifies_cli_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str] | None,
    expected: str,
    expected_url: str,
) -> None:
    monkeypatch.setattr(git_ops, "gh_available", lambda: True)
    monkeypatch.setattr(git_ops, "_run", lambda *_args, **_kwargs: result)
    actual = git_ops.create_pull_request(tmp_path, "topic", "main", "title", "body")
    assert actual.status == expected and actual.url == expected_url


def test_wiki_report_summary_lists_duplicate_and_mutation(tmp_path: Path) -> None:
    duplicate = wiki_sweep.DuplicateSlug(
        title="Same", paths=(tmp_path / "a.md", tmp_path / "b.md")
    )
    report = wiki_sweep.SweepReport(
        duplicates=(duplicate,),
        mutations=((tmp_path / "INDEX.md", "marked stale"),),
        root=tmp_path,
        index_present=True,
    )
    lines = report.summary_lines()
    assert any("duplicate slug 'Same'" in line for line in lines)
    assert any("mutation: marked stale" in line for line in lines)


def test_wiki_loaders_skip_unreadable_entry_and_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = tmp_path / "entry.md"
    index = tmp_path / wiki_sweep.INDEX_FILENAME
    entry.write_text("# Entry", encoding="utf-8")
    index.write_text("index", encoding="utf-8")
    original_read = Path.read_text

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path in {entry, index}:
            raise OSError("unreadable")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert wiki_sweep._load_entries(tmp_path) == []
    assert wiki_sweep._load_index_rows(index) == ()


def test_wiki_index_loader_skips_nonrow_after_separator(tmp_path: Path) -> None:
    index = tmp_path / wiki_sweep.INDEX_FILENAME
    index.write_text("|---|---|---|\nnot a table row\n", encoding="utf-8")
    assert wiki_sweep._load_index_rows(index) == ()


def test_wiki_parsers_and_duplicate_detector_ignore_invalid_metadata(
    tmp_path: Path,
) -> None:
    assert wiki_sweep._parse_last_updated("**Last updated:** 2026-99-99") is None
    entries = [
        wiki_sweep.WikiEntry(
            path=tmp_path / "untitled.md",
            slug="untitled",
            title=None,
            last_updated=None,
        )
    ]
    assert wiki_sweep._detect_duplicate_slugs(entries) == []


def test_wiki_stale_marker_noops_for_missing_rows_read_error_and_bad_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / wiki_sweep.INDEX_FILENAME
    index.write_text("one line\n", encoding="utf-8")
    stale = [
        wiki_sweep.StaleEntry(
            slug="old", last_updated=wiki_sweep.date(2020, 1, 1), age_days=1000
        )
    ]
    assert wiki_sweep._apply_stale_markers(index, (), stale) == 0

    row = wiki_sweep.IndexRow(
        line_no=99,
        slug="old",
        summary="summary",
        last_touched="2020-01-01",
        raw_line="| old | summary | 2020-01-01 |",
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("unreadable"))
            if path == index
            else original_read(path, *args, **kwargs)
        ),
    )
    assert wiki_sweep._apply_stale_markers(index, (row,), stale) == 0

    monkeypatch.setattr(Path, "read_text", original_read)
    assert wiki_sweep._apply_stale_markers(index, (row,), stale) == 0


@pytest.mark.parametrize("function", [git_inspect._run_git, git_inspect._run_git_bytes])
def test_git_inspect_run_helpers_collapse_spawn_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: Any
) -> None:
    monkeypatch.setattr(
        git_inspect.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("git missing")),
    )
    assert function(tmp_path, "status") is None


def test_git_inspect_common_dir_empty_relative_and_invalid_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        git_inspect, "_run_git", lambda *_args: _completed(0, stdout="")
    )
    assert git_inspect.git_common_dir(tmp_path) is None

    monkeypatch.setattr(
        git_inspect, "_run_git", lambda *_args: _completed(0, stdout=".git\n")
    )
    assert git_inspect.git_common_dir(tmp_path) == (tmp_path / ".git").resolve()
    assert git_inspect.resolve_commit(tmp_path, "main") is None
    assert git_inspect.resolve_local_branch_commit(tmp_path, " -bad") is None


def test_git_inspect_changed_paths_rejects_bad_sha_and_bad_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert git_inspect.changed_paths_since(tmp_path, "bad") is None
    responses = iter(
        [
            subprocess.CompletedProcess(["git"], 0, b"\xff\0", b""),
            subprocess.CompletedProcess(["git"], 0, b"", b""),
            subprocess.CompletedProcess(["git"], 0, b"", b""),
        ]
    )
    monkeypatch.setattr(git_inspect, "_run_git_bytes", lambda *_args: next(responses))
    assert git_inspect.changed_paths_since(tmp_path, "a" * 40) is None
    assert git_inspect._is_release_infrastructure_path(PurePosixPath()) is False


@pytest.mark.parametrize(
    ("tracked", "ignored", "expected"),
    [
        (None, None, None),
        (_completed(0), None, True),
        (_completed(2), None, None),
        (_completed(1), None, None),
        (_completed(1), _completed(0), False),
        (_completed(1), _completed(1), True),
        (_completed(1), _completed(2), None),
    ],
)
def test_git_inspect_stageable_path_classifies_git_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracked: subprocess.CompletedProcess[str] | None,
    ignored: subprocess.CompletedProcess[str] | None,
    expected: bool | None,
) -> None:
    responses = iter(
        [tracked] if tracked is None or tracked.returncode != 1 else [tracked, ignored]
    )
    monkeypatch.setattr(git_inspect, "_run_git", lambda *_args: next(responses))
    assert git_inspect.is_git_stageable_path(tmp_path, "path.txt") is expected


def test_git_inspect_blob_reader_rejects_invalid_inputs_and_git_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert git_inspect.read_commit_blob(tmp_path, "bad", "file.txt") is None
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "/file.txt") is None
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "../file.txt") is None
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "a//b") is None

    monkeypatch.setattr(git_inspect, "_run_git_bytes", lambda *_args: None)
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "file.txt") is None

    monkeypatch.setattr(
        git_inspect,
        "_run_git_bytes",
        lambda *_args: subprocess.CompletedProcess(["git"], 0, b"malformed\0", b""),
    )
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "file.txt") is None

    bad_metadata = b"not metadata\tfile.txt\0"
    monkeypatch.setattr(
        git_inspect,
        "_run_git_bytes",
        lambda *_args: subprocess.CompletedProcess(["git"], 0, bad_metadata, b""),
    )
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "file.txt") is None

    wrong_type = b"120000 blob abc\tfile.txt\0"
    monkeypatch.setattr(
        git_inspect,
        "_run_git_bytes",
        lambda *_args: subprocess.CompletedProcess(["git"], 0, wrong_type, b""),
    )
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "file.txt") is None

    calls = 0

    def blob_fail(*_args: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(
                ["git"], 0, b"100644 blob abc\tfile.txt\0", b""
            )
        return None

    monkeypatch.setattr(git_inspect, "_run_git_bytes", blob_fail)
    assert git_inspect.read_commit_blob(tmp_path, "a" * 40, "file.txt") is None


def test_git_inspect_parsers_skip_malformed_log_count_branch_and_numstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert git_inspect._parse_log_line("short") is None
    monkeypatch.setattr(
        git_inspect, "_run_git", lambda *_args: _completed(0, stdout="")
    )
    assert git_inspect.ahead_behind(tmp_path, "branch", "main") is None
    monkeypatch.setattr(
        git_inspect, "_run_git", lambda *_args: _completed(0, stdout="one\n")
    )
    assert git_inspect.ahead_behind(tmp_path, "branch", "main") is None
    monkeypatch.setattr(
        git_inspect, "_run_git", lambda *_args: _completed(0, stdout="x y\n")
    )
    assert git_inspect.ahead_behind(tmp_path, "branch", "main") is None

    monkeypatch.setattr(
        git_inspect,
        "_run_git",
        lambda *_args: _completed(0, stdout="malformed branch row\n"),
    )
    assert git_inspect.list_task_branches(tmp_path, None) == []
    assert git_inspect._numstat(tmp_path, "branch", "main")["files"] == []
