"""SPEC §17.1 — workflow and config parsing conformance."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.errors import (
    MissingWorkflowFile,
    MissingTrackerApiKey,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
    WorkflowFrontMatterNotAMap,
    WorkflowParseError,
    ConfigValidationError,
)
from symphony.workflow import (
    DEFAULT_ACTIVE_STATES,
    DEFAULT_TERMINAL_STATES,
    DEFAULT_POLL_INTERVAL_MS,
    build_service_config,
    load_workflow,
    parse_workflow_text,
    resolve_var_indirection,
    resolve_workflow_path,
    validate_for_dispatch,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_resolve_workflow_path_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_workflow_path(None) == tmp_path / "WORKFLOW.md"


def test_resolve_workflow_path_explicit(tmp_path):
    explicit = tmp_path / "alt.md"
    assert resolve_workflow_path(str(explicit)) == explicit.expanduser().resolve()


def test_missing_workflow_file(tmp_path):
    with pytest.raises(MissingWorkflowFile):
        load_workflow(tmp_path / "nope.md")


def test_parse_no_front_matter():
    wf = parse_workflow_text("Hello body\nmore", Path("/tmp/W.md"))
    assert wf.config == {}
    assert wf.prompt_template == "Hello body\nmore"


def test_parse_with_front_matter():
    text = textwrap.dedent(
        """\
        ---
        tracker:
          kind: linear
          project_slug: demo
        polling:
          interval_ms: 5000
        ---

        Prompt body for {{ issue.identifier }}
        """
    )
    wf = parse_workflow_text(text, Path("/tmp/W.md"))
    assert wf.config["tracker"]["kind"] == "linear"
    assert wf.config["polling"]["interval_ms"] == 5000
    assert wf.prompt_template.startswith("Prompt body for")


def test_parse_invalid_yaml():
    text = "---\nthis: : invalid : yaml\n---\nBody"
    with pytest.raises(WorkflowParseError):
        parse_workflow_text(text, Path("/tmp/W.md"))


def test_parse_front_matter_not_a_map():
    text = "---\n- just\n- a\n- list\n---\nBody"
    with pytest.raises(WorkflowFrontMatterNotAMap):
        parse_workflow_text(text, Path("/tmp/W.md"))


def test_var_indirection(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    assert resolve_var_indirection("$MY_TOKEN") == "secret-value"
    monkeypatch.delenv("MY_TOKEN", raising=False)
    assert resolve_var_indirection("$MY_TOKEN") == ""
    # Non-$ prefixed strings are passed through unchanged.
    assert resolve_var_indirection("$VAR more text") == "$VAR more text"


def test_build_service_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test_token")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: my-proj
            ---
            Hello {{ issue.identifier }}
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.poll_interval_ms == DEFAULT_POLL_INTERVAL_MS
    assert cfg.tracker.active_states == DEFAULT_ACTIVE_STATES
    assert cfg.tracker.terminal_states == DEFAULT_TERMINAL_STATES
    assert "Human Review" in cfg.tracker.terminal_states
    assert "Human Review" not in cfg.tracker.active_states
    assert cfg.tracker.api_key == "lin_test_token"
    assert cfg.tracker.project_slug == "my-proj"
    assert cfg.codex.command == "codex app-server"
    assert cfg.codex.model == "gpt-5.5"
    assert cfg.codex.reasoning_effort == "high"
    assert cfg.agent.max_concurrent_agents == 1
    assert cfg.agent.max_attempts == 3
    assert cfg.agent.feature_base_branch == ""
    assert cfg.agent.auto_merge_push_target is True
    assert cfg.prompt_template_for_state("Todo") == "Hello {{ issue.identifier }}"


def test_build_service_config_parses_local_only_auto_merge_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test_token")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: my-proj
            agent:
              auto_merge_push_target: false
            ---
            Prompt
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.auto_merge_push_target is False


def test_build_service_config_reads_tracker_network_timeout_seconds(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: my-proj
              api_key: lin_test_token
              network_timeout_seconds: 7.5
            ---
            Body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.tracker.network_timeout_seconds == 7.5


def test_build_service_config_rejects_invalid_tracker_network_timeout_seconds(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: my-proj
              api_key: lin_test_token
              network_timeout_seconds: 0
            ---
            Body
            """
        ),
    )

    with pytest.raises(ConfigValidationError, match="tracker.network_timeout_seconds"):
        build_service_config(load_workflow(path))


def test_build_service_config_reads_branch_policy_fields(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              feature_base_branch: dev
              auto_merge_target_branch: release
            ---
            Body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.feature_base_branch == "dev"
    assert cfg.agent.auto_merge_target_branch == "release"


def test_build_service_config_reads_agent_max_attempts(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              max_attempts: 3
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_attempts == 3


def test_build_service_config_defaults_auto_triage_actionable_todo_on(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.auto_triage_actionable_todo is True


def test_build_service_config_defaults_auto_recover_blocked_on(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.auto_recover_blocked is True


def test_build_service_config_reads_auto_recover_blocked(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              auto_recover_blocked: false
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.auto_recover_blocked is False


def test_build_service_config_reads_auto_triage_actionable_todo(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              auto_triage_actionable_todo: false
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.auto_triage_actionable_todo is False


def test_build_service_config_reads_state_token_budgets(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              max_total_tokens: 10000000
              max_total_tokens_by_state:
                "In Progress": 100000000
                Invalid: 0
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_total_tokens == 10_000_000
    assert cfg.agent.max_total_tokens_by_state == {
        "in progress": 100_000_000,
    }


def test_build_service_config_reads_state_token_attention_thresholds(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              token_attention_threshold_by_state:
                "In Progress": 2500000
                Learn: 500000
                Invalid: 0
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.token_attention_threshold_by_state == {
        "in progress": 2_500_000,
        "learn": 500_000,
    }
    assert cfg.agent.max_total_tokens == 0
    assert cfg.agent.max_total_tokens_by_state == {}


def test_build_service_config_reads_state_turn_caps(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              max_state_turns: 30
              max_state_turns_by_state:
                "In Progress": 6
                " Verify ": 3
                ignored: 0
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_state_turns == 30
    assert cfg.agent.max_state_turns_by_state == {
        "in progress": 6,
        "verify": 3,
    }


def test_build_service_config_reads_agent_stage_kinds(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              kind: claude
              stage_kinds:
                Todo: gemini
                " Verify ": Antigravity
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    # Keys are lowercased, values canonicalized (antigravity -> agy).
    assert cfg.agent.stage_kinds == {"todo": "gemini", "verify": "agy"}
    # Resolution helper: ticket pin > stage map > workflow default.
    assert cfg.agent.kind_for_state("Todo") == "gemini"
    assert cfg.agent.kind_for_state(" VERIFY ") == "agy"
    assert cfg.agent.kind_for_state("In Progress") == "claude"
    assert cfg.agent.kind_for_state("Todo", "codex") == "codex"


def test_agent_stage_kinds_default_empty(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.agent.stage_kinds == {}
    assert cfg.agent.kind_for_state("Todo") == cfg.agent.kind


def test_agent_stage_kinds_rejects_unsupported_kind(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              stage_kinds:
                Todo: hal9000
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="agent.stage_kinds"):
        build_service_config(load_workflow(path))


def test_agent_stage_kinds_rejects_non_map(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              stage_kinds: gemini
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="must be a map"):
        build_service_config(load_workflow(path))


def test_agent_stage_kinds_unknown_state_warns_but_loads(tmp_path):
    """States are user-editable, so a stale mapping key must not brick the
    workflow load — it only surfaces a load-time warning."""
    import io

    from symphony.logging import get_logger

    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              stage_kinds:
                Nonexistent: pi
            ---
            body
            """
        ),
    )
    buf = io.StringIO()
    logger = get_logger()
    logger.add_stream(buf)
    try:
        cfg = build_service_config(load_workflow(path))
    finally:
        logger._streams.remove(buf)

    assert cfg.agent.stage_kinds == {"nonexistent": "pi"}
    assert "agent_stage_kinds_unknown_state" in buf.getvalue()


def test_max_total_tokens_defaults_disabled_for_reasoning_heavy_work(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_total_tokens == 0
    assert cfg.agent.max_total_tokens_by_state == {}
    assert cfg.agent.token_attention_threshold_by_state == {}


def test_build_service_config_reads_compact_issue_context(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              compact_issue_context: true
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.compact_issue_context is True


def test_build_service_config_compact_issue_context_defaults_true(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.compact_issue_context is True


def test_build_service_config_compact_issue_context_can_opt_out(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              compact_issue_context: false
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.compact_issue_context is False


def test_build_service_config_crash_continuation_defaults_true(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.crash_continuation is True


def test_build_service_config_crash_continuation_can_opt_out(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              crash_continuation: false
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.crash_continuation is False


def test_build_service_config_scheduling_policy_defaults_fifo(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.scheduling_policy == "fifo"


def test_build_service_config_scheduling_policy_accepts_dag(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              scheduling_policy: dag
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.scheduling_policy == "dag"


def test_build_service_config_scheduling_policy_rejects_unknown_value(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              scheduling_policy: magic
            ---
            Hello
            """
        ),
    )

    with pytest.raises(ConfigValidationError, match="scheduling_policy"):
        build_service_config(load_workflow(path))


def test_build_service_config_reads_codex_model_and_reasoning(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            codex:
              model: gpt-5.4
              reasoning_effort: medium
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.codex.model == "gpt-5.4"
    assert cfg.codex.reasoning_effort == "medium"


def test_repo_workflow_codex_is_browser_capable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    cfg = build_service_config(load_workflow(repo_root / "WORKFLOW.md"))

    assert cfg.codex.thread_sandbox == "danger-full-access"
    assert cfg.codex.turn_sandbox_policy == "danger-full-access"


def test_build_service_config_reads_agent_max_reopens(tmp_path):
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: file
  board_root: ./kanban
agent:
  kind: codex
  max_reopens: 5
---
""",
        encoding="utf-8",
    )
    cfg = build_service_config(load_workflow(workflow_path))
    assert cfg.agent.max_reopens == 5


def test_build_service_config_defaults_agent_max_reopens(tmp_path):
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """---
tracker:
  kind: file
  board_root: ./kanban
agent:
  kind: codex
---
""",
        encoding="utf-8",
    )
    cfg = build_service_config(load_workflow(workflow_path))
    assert cfg.agent.max_reopens == 3


def test_build_service_config_allows_zero_agent_max_attempts(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              max_attempts: 0
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_attempts == 0


def test_stage_prompt_files_are_loaded_relative_to_workflow(tmp_path):
    prompt_dir = tmp_path / "docs" / "prompts" / "stages"
    prompt_dir.mkdir(parents=True)
    (tmp_path / "docs" / "prompts" / "base.md").write_text(
        "BASE for {{ issue.identifier }}", encoding="utf-8"
    )
    (prompt_dir / "todo.md").write_text(
        "TODO rules for {{ issue.state }}", encoding="utf-8"
    )
    (prompt_dir / "explore.md").write_text(
        "EXPLORE rules for {{ issue.state }}", encoding="utf-8"
    )
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
              active_states: [Todo, Explore]
            prompts:
              base: ./docs/prompts/base.md
              stages:
                Todo: ./docs/prompts/stages/todo.md
                Explore: ./docs/prompts/stages/explore.md
            ---
            LEGACY all-stage prompt
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    todo_prompt = cfg.prompt_template_for_state("Todo")
    explore_prompt = cfg.prompt_template_for_state("explore")
    assert todo_prompt == "BASE for {{ issue.identifier }}\n\nTODO rules for {{ issue.state }}"
    assert "EXPLORE rules" not in todo_prompt
    assert explore_prompt == (
        "BASE for {{ issue.identifier }}\n\nEXPLORE rules for {{ issue.state }}"
    )
    assert "LEGACY all-stage prompt" not in todo_prompt


def test_missing_stage_prompt_file_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            prompts:
              stages:
                Todo: ./docs/prompts/stages/missing.md
            ---
            legacy prompt
            """
        ),
    )

    with pytest.raises(ConfigValidationError, match="prompt file not found"):
        build_service_config(load_workflow(path))


def test_build_service_config_workspace_root_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            workspace:
              root: ./ws
            ---
            body
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.workspace_root == (tmp_path / "ws").resolve()


def test_build_service_config_reads_workspace_reuse_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            workspace:
              root: ./ws
              reuse_policy: refresh
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.workspace_reuse_policy == "refresh"


def test_build_service_config_rejects_unknown_workspace_reuse_policy(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            workspace:
              reuse_policy: delete-everything
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="workspace.reuse_policy"):
        build_service_config(load_workflow(path))


def test_build_service_config_state_concurrency_normalization(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            agent:
              max_concurrent_agents_by_state:
                "Todo": 3
                "In Progress": "not-an-int"
                "Bad": -2
            ---
            body
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.agent.max_concurrent_agents_by_state == {"todo": 3}


def test_validate_for_dispatch_unsupported_tracker(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: unknown_tracker, project_slug: x, api_key: xx }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(UnsupportedTrackerKind):
        validate_for_dispatch(cfg)


def test_validate_for_dispatch_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerApiKey):
        validate_for_dispatch(cfg)


def test_validate_for_dispatch_missing_project_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerProjectSlug):
        validate_for_dispatch(cfg)


def test_state_descriptions_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.state_descriptions == {}


def test_state_descriptions_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
              state_descriptions:
                Todo: "  Triage incoming work  "
                "In Progress": Code + tests
                Review: Self-review the diff
                Empty: ""
                42: "non-string key dropped"
                Bogus: 123
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    # Keys lowercased, blank/non-string values dropped, non-string keys dropped,
    # leading/trailing whitespace stripped.
    assert cfg.tracker.state_descriptions == {
        "todo": "Triage incoming work",
        "in progress": "Code + tests",
        "review": "Self-review the diff",
    }


def test_state_descriptions_invalid_root_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
              state_descriptions: not-a-dict
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.state_descriptions == {}


def test_invalid_max_turns_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            agent: { max_turns: 0 }
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


def test_default_max_total_turns_is_two_hundred(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: file, board_root: ./kanban }
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_total_turns == 200


def test_default_no_stage_change_watchdog_is_block_after_thirty_turns(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: file, board_root: ./kanban }
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_state_turns == 30
    assert cfg.agent.no_stage_change_action == "block"


def test_no_stage_change_watchdog_can_disable_or_move_to_state(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              active_states: [Todo, In Progress, Verify]
              terminal_states: [Done, Blocked]
            agent:
              max_state_turns: 0
              no_stage_change_action: Verify
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_state_turns == 0
    assert cfg.agent.no_stage_change_action == "Verify"


def test_hooks_warning_policy_defaults_to_nonfatal_and_can_opt_in(tmp_path):
    default_path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: file, board_root: ./kanban }
            ---
            body
            """
        ),
    )
    default_cfg = build_service_config(load_workflow(default_path))
    strict_path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: file, board_root: ./kanban }
            hooks:
              fail_on_warning_patterns: true
            ---
            body
            """
        ),
    )

    strict_cfg = build_service_config(load_workflow(strict_path))

    assert default_cfg.hooks.fail_on_warning_patterns is False
    assert strict_cfg.hooks.fail_on_warning_patterns is True


def test_no_stage_change_action_must_be_block_or_configured_state(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              active_states: [Todo, In Progress, Verify]
              terminal_states: [Done, Blocked]
            agent:
              no_stage_change_action: Missing
            ---
            body
            """
        ),
    )

    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


# --- positive-int validation tightened in improve/observability-and-doctor ---

@pytest.mark.parametrize("field,raw_value", [
    ("max_concurrent_agents", 0),
    ("max_concurrent_agents", -1),
    ("max_retry_backoff_ms", 0),
    ("max_retry_backoff_ms", -100),
    ("max_attempts", -1),
])
def test_invalid_agent_int_fields_fail_validation(tmp_path, field, raw_value):
    """Regression: previously these silently accepted 0/negative via
    `_as_int`, leading to footguns (max_concurrent_agents=0 dispatches
    nothing; max_retry_backoff_ms=0 produces a tight retry loop)."""
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            agent: {{ {field}: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("kind,field", [
    ("pi", "turn_timeout_ms"),
    ("pi", "read_timeout_ms"),
    ("claude", "turn_timeout_ms"),
    ("codex", "turn_timeout_ms"),
    ("gemini", "stall_timeout_ms"),
])
def test_invalid_backend_timeouts_fail_validation(tmp_path, kind, field):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            agent: {{ kind: {kind} }}
            {kind}: {{ {field}: 0 }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


def test_invalid_polling_interval_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            polling: { interval_ms: 0 }
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


# ---------------------------------------------------------------------------
# continuous_improvement
# ---------------------------------------------------------------------------


def test_continuous_improvement_defaults_disabled(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.continuous_improvement.enabled is False
    assert cfg.continuous_improvement.interval_ms == 1_800_000
    assert cfg.continuous_improvement.max_turns == 48
    assert cfg.continuous_improvement.ticket_prefix == "CI"
    assert cfg.continuous_improvement.max_tickets_per_run == 5
    assert cfg.continuous_improvement.require_idle_board is True
    assert cfg.continuous_improvement.agent_kind == ""


def test_continuous_improvement_agent_kind_accepted_and_normalized(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              agent_kind: "  Claude  "
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.continuous_improvement.agent_kind == "claude"


@pytest.mark.parametrize("raw_value", ["\"nope\"", "\"Bogus\""])
def test_continuous_improvement_agent_kind_rejects_unknown(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ agent_kind: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.agent_kind"):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("raw_value", ["1", "true"])
def test_continuous_improvement_agent_kind_rejects_non_string(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ agent_kind: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.agent_kind"):
        build_service_config(load_workflow(path))


def test_continuous_improvement_reads_configured_values(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              enabled: true
              interval_ms: 120000
              max_turns: 10
              ticket_prefix: HB
              max_tickets_per_run: 2
              require_idle_board: false
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    ci = cfg.continuous_improvement
    assert ci.enabled is True
    assert ci.interval_ms == 120_000
    assert ci.max_turns == 10
    assert ci.ticket_prefix == "HB"
    assert ci.max_tickets_per_run == 2
    assert ci.require_idle_board is False


def test_continuous_improvement_modes_default_to_readiness_only(tmp_path):
    """Back-compat: `enabled: true` with no `modes:` is the old heartbeat."""
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              enabled: true
            ---
            body
            """
        ),
    )
    ci = build_service_config(load_workflow(path)).continuous_improvement
    assert ci.modes == ()
    assert ci.resolved_modes() == ("readiness",)
    assert ci.max_improvement_tickets_per_run == 3


def test_continuous_improvement_disabled_resolves_no_modes(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              enabled: false
              modes: [readiness, market_research]
            ---
            body
            """
        ),
    )
    ci = build_service_config(load_workflow(path)).continuous_improvement
    assert ci.modes == ("readiness", "market_research")
    assert ci.resolved_modes() == ()


def test_continuous_improvement_modes_normalize_and_order(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              enabled: true
              modes: [Security, blocked_fixes, security]
              mode_interval_hours:
                market_research: 12
                security: 0
              max_improvement_tickets_per_run: 2
            ---
            body
            """
        ),
    )
    ci = build_service_config(load_workflow(path)).continuous_improvement
    assert ci.modes == ("blocked_fixes", "security")
    assert ci.resolved_modes() == ("blocked_fixes", "security")
    assert ci.interval_hours_for("market_research") == 12.0
    assert ci.interval_hours_for("security") == 0.0
    # Unset modes keep the shipped cadence default.
    assert ci.interval_hours_for("feature_improvements") == 72.0
    assert ci.max_improvement_tickets_per_run == 2


@pytest.mark.parametrize(
    "raw_value",
    ["[bogus]", "readiness", "[1]", "{}"],
)
def test_continuous_improvement_modes_reject_bad_values(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ modes: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize(
    "raw_value",
    ["{ bogus: 3 }", "{ security: -1 }", "{ security: \"3\" }", "[security]"],
)
def test_continuous_improvement_mode_interval_hours_reject_bad_values(
    tmp_path, raw_value
):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ mode_interval_hours: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


def test_continuous_improvement_max_turns_zero_means_unlimited(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement:
              max_turns: 0
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.continuous_improvement.max_turns == 0


@pytest.mark.parametrize("raw_value", ["\"false\"", "1", "0", "\"true\""])
def test_continuous_improvement_enabled_rejects_non_bool(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ enabled: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.enabled"):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("raw_value", ["true", "false", "\"1800000\"", "0", "-1", "1.5"])
def test_continuous_improvement_interval_ms_rejects_invalid(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ interval_ms: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.interval_ms"):
        build_service_config(load_workflow(path))


def test_continuous_improvement_interval_ms_enforces_lower_bound(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement: { interval_ms: 59999 }
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.interval_ms"):
        build_service_config(load_workflow(path))


def test_continuous_improvement_interval_ms_accepts_lower_bound(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            continuous_improvement: { interval_ms: 60000 }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.continuous_improvement.interval_ms == 60_000


@pytest.mark.parametrize("raw_value", ["true", "false", "\"48\"", "-1"])
def test_continuous_improvement_max_turns_rejects_invalid(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ max_turns: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError, match="continuous_improvement.max_turns"):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("raw_value", ["true", "false", "\"5\"", "0", "-1"])
def test_continuous_improvement_max_tickets_per_run_rejects_invalid(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ max_tickets_per_run: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(
        ConfigValidationError, match="continuous_improvement.max_tickets_per_run"
    ):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("raw_value", ["\"false\"", "1", "0"])
def test_continuous_improvement_require_idle_board_rejects_non_bool(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ require_idle_board: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(
        ConfigValidationError, match="continuous_improvement.require_idle_board"
    ):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("raw_value", ["\"1CI\"", "\"CI-1\"", "\"\"", "123"])
def test_continuous_improvement_ticket_prefix_rejects_invalid(tmp_path, raw_value):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            continuous_improvement: {{ ticket_prefix: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(
        ConfigValidationError, match="continuous_improvement.ticket_prefix"
    ):
        build_service_config(load_workflow(path))


# ---------------------------------------------------------------------------
# F-02 / review §4.2(2) — agent.stall_timeout_ms_by_state
# ---------------------------------------------------------------------------


def test_build_service_config_reads_stall_timeout_ms_by_state(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              active_states: [Todo, In Progress, Verify]
              terminal_states: [Done]
            agent:
              kind: claude
              stall_timeout_ms_by_state:
                Verify: 900000
                " In Progress ": 600000
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.stall_timeout_ms_by_state == {
        "verify": 900_000,
        "in progress": 600_000,
    }
    assert cfg.agent.stall_timeout_ms_for_state("Verify", 300_000) == 900_000
    assert cfg.agent.stall_timeout_ms_for_state("Todo", 300_000) == 300_000
    assert cfg.agent.stall_timeout_ms_for_state(None, 300_000) == 300_000


def test_stall_timeout_ms_by_state_defaults_empty(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.stall_timeout_ms_by_state == {}


def test_stall_timeout_ms_by_state_drops_invalid_values(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              active_states: [Todo, Verify]
              terminal_states: [Done]
            agent:
              stall_timeout_ms_by_state:
                Verify: 900000
                Todo: 0
                Bogus: not-a-number
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.stall_timeout_ms_by_state == {"verify": 900_000}


def test_stall_timeout_ms_by_state_rejects_non_map(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              stall_timeout_ms_by_state: 900000
            ---
            body
            """
        ),
    )

    with pytest.raises(ConfigValidationError, match="stall_timeout_ms_by_state"):
        build_service_config(load_workflow(path))


def test_stall_timeout_ms_by_state_unknown_state_warns_but_loads(tmp_path):
    import io

    from symphony.logging import get_logger

    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              active_states: [Todo]
              terminal_states: [Done]
            agent:
              stall_timeout_ms_by_state:
                Nonexistent: 900000
            ---
            body
            """
        ),
    )
    buf = io.StringIO()
    logger = get_logger()
    logger.add_stream(buf)
    try:
        cfg = build_service_config(load_workflow(path))
    finally:
        logger._streams.remove(buf)

    assert cfg.agent.stall_timeout_ms_by_state == {"nonexistent": 900_000}
    assert "agent_stall_timeout_unknown_state" in buf.getvalue()


# ---------------------------------------------------------------------------
# F-06 — agent.stage_contracts: auto | on | off (explicit + observable)
# ---------------------------------------------------------------------------


def _contracts_cfg(tmp_path, *, lanes: str, mode: str | None = None):
    setting = f"  stage_contracts: {mode}\n" if mode is not None else ""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return _write(
        tmp_path,
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "  board_root: ./kanban\n"
        f"  active_states: [{lanes}]\n"
        "  terminal_states: [Done]\n"
        "agent:\n"
        "  kind: codex\n"
        "  max_turns: 20\n"
        f"{setting}"
        "---\n"
        "body\n",
    )


def test_stage_contracts_defaults_to_auto_and_follows_the_lane_heuristic(tmp_path):
    default_board = build_service_config(
        load_workflow(_contracts_cfg(tmp_path / "a", lanes="Todo, In Progress, Verify, Document"))
    )
    assert default_board.agent.stage_contracts == "auto"
    assert default_board.agent.stage_contracts_enabled(
        default_board.tracker.active_states
    )

    renamed = build_service_config(
        load_workflow(_contracts_cfg(tmp_path / "b", lanes="Todo, In Progress, Verify, Docs"))
    )
    assert not renamed.agent.stage_contracts_enabled(renamed.tracker.active_states)


def test_stage_contracts_on_enforces_a_renamed_lane_board(tmp_path):
    cfg = build_service_config(
        load_workflow(
            _contracts_cfg(tmp_path, lanes="Todo, In Progress, Verify, Docs", mode="on")
        )
    )
    assert cfg.agent.stage_contracts_enabled(cfg.tracker.active_states)


def test_stage_contracts_off_disables_a_default_lane_board(tmp_path):
    cfg = build_service_config(
        load_workflow(
            _contracts_cfg(
                tmp_path, lanes="Todo, In Progress, Verify, Document", mode="off"
            )
        )
    )
    assert not cfg.agent.stage_contracts_enabled(cfg.tracker.active_states)


def test_stage_contracts_rejects_unknown_mode(tmp_path):
    path = _contracts_cfg(tmp_path, lanes="Todo, In Progress", mode="sometimes")
    with pytest.raises(ConfigValidationError, match="stage_contracts"):
        build_service_config(load_workflow(path))


def test_stage_contracts_auto_disable_is_logged_once_per_load(tmp_path):
    """Renaming a lane silently removed the evidence floor — now it is loud."""
    import io

    from symphony.logging import get_logger

    path = _contracts_cfg(tmp_path, lanes="Todo, In Progress, Verify, Docs")
    buf = io.StringIO()
    logger = get_logger()
    logger.add_stream(buf)
    try:
        build_service_config(load_workflow(path))
    finally:
        logger._streams.remove(buf)

    output = buf.getvalue()
    assert "stage_contracts_disabled" in output
    assert "Docs" in output


def test_stage_contracts_enabled_board_logs_nothing(tmp_path):
    import io

    from symphony.logging import get_logger

    path = _contracts_cfg(tmp_path, lanes="Todo, In Progress, Verify, Document")
    buf = io.StringIO()
    logger = get_logger()
    logger.add_stream(buf)
    try:
        build_service_config(load_workflow(path))
    finally:
        logger._streams.remove(buf)

    assert "stage_contracts_disabled" not in buf.getvalue()


def test_preview_config_without_command_stays_unconfigured_and_disabled(tmp_path):
    cfg = build_service_config(load_workflow(_write(tmp_path, "Body")))
    assert cfg.preview.enabled is False
    assert cfg.preview.cwd == "."
    assert cfg.preview.acceptance == ()


def test_preview_configured_command_defaults_enabled(tmp_path):
    path = _write(
        tmp_path,
        "---\npreview:\n  command: python3 -m http.server ${PORT} --bind ${HOST}\n---\nBody",
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.preview.enabled is True


def test_preview_configured_command_can_be_explicitly_disabled(tmp_path):
    path = _write(
        tmp_path,
        "---\npreview:\n  enabled: false\n  command: custom-preview-command\n---\nBody",
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.preview.enabled is False


def test_preview_config_parses_trusted_command_and_acceptance(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            preview:
              enabled: true
              cwd: todo-app
              command: python3 -m http.server ${PORT} --bind ${HOST}
              health_path: /
              url_path: /index.html
              startup_timeout_ms: 5000
              release_ticket: SMA-32
              acceptance: [Add a todo, Persists after reload]
            ---
            Body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.preview.enabled is True
    assert cfg.preview.cwd == "todo-app"
    assert cfg.preview.release_ticket == "SMA-32"
    assert cfg.preview.acceptance == ("Add a todo", "Persists after reload")


@pytest.mark.parametrize("cwd", ["../outside", "/tmp/outside"])
def test_preview_config_rejects_checkout_escape(tmp_path, cwd):
    path = _write(
        tmp_path,
        f"---\npreview:\n  enabled: true\n  cwd: {cwd}\n  command: python3 -m http.server ${{PORT}}\n---\nBody",
    )
    with pytest.raises(ConfigValidationError, match="preview.cwd"):
        build_service_config(load_workflow(path))


def test_preview_config_requires_managed_loopback_host(tmp_path):
    path = _write(
        tmp_path,
        "---\npreview:\n  enabled: true\n  command: python3 -m http.server ${PORT}\n---\nBody",
    )
    with pytest.raises(ConfigValidationError, match=r"include \$\{HOST\}"):
        build_service_config(load_workflow(path))


def test_artifacts_config_defaults(tmp_path):
    cfg = build_service_config(load_workflow(_write(tmp_path, "Body")))
    assert cfg.artifacts.enabled is True
    assert cfg.artifacts.dir == ".symphony-artifacts"
    assert cfg.artifacts.max_file_mb == 25
    assert cfg.artifacts.max_ticket_mb == 200
    assert cfg.artifacts.ttl_days == 30
    assert cfg.artifacts.require_for_done is False


def test_artifacts_config_parses_overrides(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            artifacts:
              enabled: false
              dir: deliverables
              max_file_mb: 5
              max_ticket_mb: 50
              ttl_days: 0
              require_for_done: true
            ---
            Body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.artifacts.enabled is False
    assert cfg.artifacts.dir == "deliverables"
    assert cfg.artifacts.max_file_mb == 5
    assert cfg.artifacts.max_ticket_mb == 50
    assert cfg.artifacts.ttl_days == 0
    assert cfg.artifacts.require_for_done is True


@pytest.mark.parametrize("bad_dir", ["../outside", "/tmp/abs", "a/b", "."])
def test_artifacts_config_rejects_unsafe_dir(tmp_path, bad_dir):
    path = _write(tmp_path, f"---\nartifacts:\n  dir: {bad_dir}\n---\nBody")
    with pytest.raises(ConfigValidationError, match="artifacts.dir"):
        build_service_config(load_workflow(path))


def test_artifacts_config_rejects_negative_ttl(tmp_path):
    path = _write(tmp_path, "---\nartifacts:\n  ttl_days: -1\n---\nBody")
    with pytest.raises(ConfigValidationError, match="artifacts.ttl_days"):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("bad_dir", ["#drop", "!keep", "a*b", "a?b", "a[b]"])
def test_artifacts_config_rejects_gitignore_metacharacters(tmp_path, bad_dir):
    """`dir` is written verbatim into .git/info/exclude.

    `#drop` would become a comment and `!keep` a negation, silently
    disabling the rule that keeps deliverables out of the merge.
    """
    path = _write(tmp_path, f'---\nartifacts:\n  dir: "{bad_dir}"\n---\nBody')
    with pytest.raises(ConfigValidationError, match="artifacts.dir"):
        build_service_config(load_workflow(path))
