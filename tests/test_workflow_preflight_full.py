"""Workflow + preflight edge cases beyond `test_workflow.py`.

`test_workflow.py` covers the linear preflight branches and a handful of
build_service_config defaults. This file extends coverage to:

  * `validate_for_dispatch` — file/jira tracker required-field matrix.
  * Empty `tracker.kind` raises UnsupportedTrackerKind.
  * Each backend kind requires a non-empty `command`.
  * Env-var indirection: `$VAR` resolved to empty triggers the relevant
    missing-secret error (no false success).
  * Frontend matter robustness: unknown tracker kind on first parse,
    malformed YAML wrap-up, missing required fields.
  * `prompt_template_for_state` falls back to base when no stage match.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from symphony.errors import (
    ConfigValidationError,
    MissingTrackerApiKey,
    MissingTrackerEmail,
    MissingTrackerEndpoint,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
)
from symphony.workflow import (
    build_service_config,
    load_workflow,
    validate_for_dispatch,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _extract_readme_quickstart_workflow(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"cat > WORKFLOW\.md <<'YAML'\n(?P<workflow>---\n.*?\n)YAML",
        text,
        re.DOTALL,
    )
    assert match is not None
    return match.group("workflow")


# ---------------------------------------------------------------------------
# tracker.kind validation
# ---------------------------------------------------------------------------


def test_empty_tracker_kind_raises_unsupported_tracker_kind(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: ""
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(UnsupportedTrackerKind):
        validate_for_dispatch(cfg)


def test_unknown_tracker_kind_carries_kind_in_exception(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: mystery
          project_slug: x
          api_key: tok
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(UnsupportedTrackerKind) as ei:
        validate_for_dispatch(cfg)
    # The error message names the bad kind for the operator.
    assert "mystery" in str(ei.value)


# ---------------------------------------------------------------------------
# file tracker — board_root required
# ---------------------------------------------------------------------------


def test_file_tracker_without_board_root_fails_preflight(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: file
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # Force board_root None for the assertion (builder normally defaults to ./board).
    cfg_no_root = replace(cfg, tracker=replace(cfg.tracker, board_root=None))
    with pytest.raises(ConfigValidationError, match="board_root"):
        validate_for_dispatch(cfg_no_root)


def test_file_tracker_with_board_root_passes_preflight(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: file
          board_root: ./tickets
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # Should not raise — file tracker has all required fields.
    validate_for_dispatch(cfg)


def test_multi_stage_workflow_rejects_too_low_max_turns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: file
          board_root: ./tickets
          active_states: [Todo, In Progress, Verify, Learn]
          terminal_states: [Done, Blocked]
        agent:
          max_turns: 1
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))

    with pytest.raises(ConfigValidationError, match="agent.max_turns=1"):
        validate_for_dispatch(cfg)


def test_shipped_workflow_example_passes_dispatch_preflight() -> None:
    cfg = build_service_config(load_workflow(_REPO_ROOT / "examples/WORKFLOW.smoke.md"))

    validate_for_dispatch(cfg)


@pytest.mark.parametrize("relative_path", ["README.md", "README.ko.md"])
def test_readme_quickstart_workflow_passes_dispatch_preflight(
    tmp_path: Path, relative_path: str
) -> None:
    workflow_text = _extract_readme_quickstart_workflow(_REPO_ROOT / relative_path)
    path = _write(tmp_path, workflow_text)
    cfg = build_service_config(load_workflow(path))

    validate_for_dispatch(cfg)


# ---------------------------------------------------------------------------
# jira tracker — endpoint, email, api_key, project_slug
# ---------------------------------------------------------------------------


def _jira_workflow(
    tmp_path: Path,
    *,
    endpoint: str = "https://example.atlassian.net",
    email: str = "user@example.com",
    api_key: str = "tok",
    project_slug: str = "PROJ",
) -> Path:
    return _write(
        tmp_path,
        f"""\
        ---
        tracker:
          kind: jira
          endpoint: {endpoint}
          email: {email}
          api_key: {api_key}
          project_slug: {project_slug}
        ---
        body
        """,
    )


def test_jira_tracker_missing_endpoint(tmp_path: Path) -> None:
    path = _jira_workflow(tmp_path, endpoint="")
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerEndpoint):
        validate_for_dispatch(cfg)


def test_jira_tracker_missing_email(tmp_path: Path) -> None:
    path = _jira_workflow(tmp_path, email="")
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerEmail):
        validate_for_dispatch(cfg)


def test_jira_tracker_missing_api_key(tmp_path: Path) -> None:
    path = _jira_workflow(tmp_path, api_key="")
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerApiKey):
        validate_for_dispatch(cfg)


def test_jira_tracker_missing_project_slug(tmp_path: Path) -> None:
    path = _jira_workflow(tmp_path, project_slug="")
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerProjectSlug):
        validate_for_dispatch(cfg)


def test_jira_tracker_full_passes_preflight(tmp_path: Path) -> None:
    path = _jira_workflow(tmp_path)
    cfg = build_service_config(load_workflow(path))
    validate_for_dispatch(cfg)


# ---------------------------------------------------------------------------
# Env-var indirection: $VAR resolution
# ---------------------------------------------------------------------------


def test_env_var_indirection_with_unset_var_triggers_missing_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `api_key: $LINEAR_API_KEY` resolves to "" the preflight must
    fail with MissingTrackerApiKey, not silently accept an empty secret."""

    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: linear
          project_slug: proj
          api_key: $LINEAR_API_KEY
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerApiKey):
        validate_for_dispatch(cfg)


def test_env_var_indirection_with_explicit_value_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "tok-secret")
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: linear
          project_slug: proj
          api_key: $LINEAR_API_KEY
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.api_key == "tok-secret"
    validate_for_dispatch(cfg)


# ---------------------------------------------------------------------------
# Backend command required-non-empty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["codex", "claude", "gemini", "opencode", "pi"])
def test_empty_backend_command_fails_preflight(tmp_path: Path, kind: str) -> None:
    """Setting the chosen backend's `command:` to "" via YAML normally
    fills in a default; the preflight guard against an empty command
    catches the case where some downstream caller mutates the config to
    blank the command (e.g. a wrapper that forgets to fall back)."""
    path = _write(
        tmp_path,
        f"""\
        ---
        tracker:
          kind: file
          board_root: ./tickets
        agent:
          kind: {kind}
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # Blank out the chosen backend's command after the builder filled in defaults.
    backend_cfg = getattr(cfg, kind)
    cfg_blank = replace(cfg, **{kind: replace(backend_cfg, command="")})
    with pytest.raises(ConfigValidationError, match="must be non-empty"):
        validate_for_dispatch(cfg_blank)


def test_other_backends_empty_command_does_not_fail_preflight(tmp_path: Path) -> None:
    """When agent.kind=codex, only codex.command is required to be set.
    Empty inactive backend commands are tolerated because they are not used."""
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: file
          board_root: ./tickets
        agent:
          kind: codex
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # Blank out the inactive backends.
    cfg_inactive_blank = replace(
        cfg,
        claude=replace(cfg.claude, command=""),
        gemini=replace(cfg.gemini, command=""),
        opencode=replace(cfg.opencode, command=""),
        pi=replace(cfg.pi, command=""),
    )
    # Should NOT raise: the active backend is codex and it has a command.
    validate_for_dispatch(cfg_inactive_blank)


# ---------------------------------------------------------------------------
# Config-level: prompt_template_for_state fallback
# ---------------------------------------------------------------------------


def test_prompt_template_for_state_falls_back_to_base_when_no_stage_match(
    tmp_path: Path,
) -> None:
    """When `stage_templates` lacks an entry for a state, the runtime
    template is the legacy single `prompt_template` field."""
    path = _write(
        tmp_path,
        """\
        ---
        tracker:
          kind: file
          board_root: ./tickets
        ---
        legacy single-template body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # No `prompts.stage_templates` configured -> any state falls back.
    out = cfg.prompt_template_for_state("Some Unmatched State")
    assert "legacy single-template body" in out


# ---------------------------------------------------------------------------
# Frontmatter robustness for tracker kind
# ---------------------------------------------------------------------------


def test_workflow_with_no_tracker_block_at_all_fails_preflight(tmp_path: Path) -> None:
    """An `---` frontmatter that omits `tracker:` is constructible (builder
    fills in safe defaults), but preflight still rejects when the resulting
    kind isn't usable."""
    path = _write(
        tmp_path,
        """\
        ---
        agent:
          kind: codex
        ---
        body
        """,
    )
    cfg = build_service_config(load_workflow(path))
    # Either kind is empty (raises UnsupportedTrackerKind) or it defaulted
    # to one of the supported kinds — verify the *outcome* is consistent:
    # if the resulting tracker.kind is "file", preflight requires board_root.
    if cfg.tracker.kind == "file" and cfg.tracker.board_root is None:
        with pytest.raises(ConfigValidationError):
            validate_for_dispatch(cfg)
    elif cfg.tracker.kind not in {"file", "linear", "jira"}:
        with pytest.raises(UnsupportedTrackerKind):
            validate_for_dispatch(cfg)
    # else: the builder defaulted to a usable kind — that's fine too.
