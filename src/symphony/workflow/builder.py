"""SPEC §5.3, §6.1 — turn a `WorkflowDefinition` into a frozen `ServiceConfig`.

This module owns the long YAML-to-dataclass projection. The shape is
deliberately flat (one function reads each top-level YAML key and
constructs the matching `*Config`) so a reader scanning by `Cmd-F`
can find every default and every validator in one place.

Strict validators (`_validated_*`) raise `ConfigValidationError`;
permissive helpers in `coercion.py` swallow malformed values into
documented defaults. The dispatch-time, harder validation lives in
`preflight.py`.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import ConfigValidationError
from ..logging import get_logger
from ..notifications import build_notifications_config
from .coercion import (
    _as_int,
    _as_str,
    _as_str_list,
    _normalize_state_description_map,
    _normalize_state_map,
    _read_prompt_file,
    _resolve_config_path,
    expand_path_value,
    resolve_var_indirection,
)
from .config import (
    AgentConfig,
    AgyConfig,
    ClaudeConfig,
    CodexConfig,
    ContinuousImprovementConfig,
    GeminiConfig,
    HooksConfig,
    KiroConfig,
    OpenCodeConfig,
    PiConfig,
    PrimeAgentConfig,
    ArtifactsConfig,
    PreviewConfig,
    ProgressConfig,
    PromptConfig,
    ServerConfig,
    ServiceConfig,
    SystemConfig,
    TrackerConfig,
    TuiConfig,
    WikiConfig,
)
from .constants import (
    _AFTER_DONE_FAILURE_POLICIES,
    DEFAULT_ACTIVE_STATES,
    DEFAULT_AGY_COMMAND,
    DEFAULT_AGENT_KIND,
    DEFAULT_AUTO_MERGE_EXCLUDE_PATHS,
    DEFAULT_AUTO_RECOVER_BLOCKED,
    DEFAULT_BACKEND_READ_TIMEOUT_MS,
    DEFAULT_BACKEND_STALL_TIMEOUT_MS,
    DEFAULT_BACKEND_TURN_TIMEOUT_MS,
    DEFAULT_BOARD_ROOT_NAME,
    DEFAULT_CI_INTERVAL_MS,
    DEFAULT_CI_MAX_IMPROVEMENT_TICKETS_PER_RUN,
    DEFAULT_CI_MAX_TICKETS_PER_RUN,
    DEFAULT_CI_MAX_TURNS,
    DEFAULT_CI_MIN_INTERVAL_MS,
    DEFAULT_CI_TICKET_PREFIX,
    DEFAULT_CLAUDE_COMMAND,
    DEFAULT_CODEX_COMMAND,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_READ_TIMEOUT_MS,
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_CODEX_STALL_TIMEOUT_MS,
    DEFAULT_CODEX_TURN_TIMEOUT_MS,
    DEFAULT_GEMINI_COMMAND,
    DEFAULT_HOOK_TIMEOUT_MS,
    DEFAULT_KIRO_COMMAND,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_CONCURRENT_AGENTS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_RETRY_BACKOFF_MS,
    DEFAULT_MAX_STATE_TURNS,
    DEFAULT_MAX_TOTAL_TURNS,
    DEFAULT_MAX_TURNS,
    DEFAULT_OPENCODE_COMMAND,
    DEFAULT_PI_COMMAND,
    DEFAULT_PRIME_AGENT_COMMAND,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_PROMPT,
    DEFAULT_TERMINAL_STATES,
    DEFAULT_WORKSPACE_REUSE_POLICY,
    JIRA_API_TOKEN_ENV,
    JIRA_EMAIL_ENV,
    LINEAR_API_KEY_ENV,
    LINEAR_DEFAULT_ENDPOINT,
    SUPPORTED_AGENT_KINDS,
    SUPPORTED_CI_MODES,
    SUPPORTED_WORKSPACE_REUSE_POLICIES,
)
from .parser import WorkflowDefinition
from .presets import board_uses_default_contracts


def _build_prompt_config(raw: Any, base_dir: Path) -> PromptConfig:
    if not isinstance(raw, dict):
        return PromptConfig()

    base_template = ""
    base_path: Path | None = None
    raw_base = raw.get("base")
    if isinstance(raw_base, str) and raw_base.strip():
        base_path = _resolve_config_path(base_dir, raw_base.strip())
        base_template = _read_prompt_file(base_path)

    stage_templates: dict[str, str] = {}
    stage_paths: dict[str, Path] = {}
    raw_stages = raw.get("stages")
    if isinstance(raw_stages, dict):
        for raw_state, raw_path in raw_stages.items():
            if not isinstance(raw_state, str):
                continue
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            key = raw_state.strip().lower()
            path = _resolve_config_path(base_dir, raw_path.strip())
            stage_paths[key] = path
            stage_templates[key] = _read_prompt_file(path)

    return PromptConfig(
        base_template=base_template,
        base_path=base_path,
        stage_templates=stage_templates,
        stage_paths=stage_paths,
    )


def _canonical_agent_kind(kind: str) -> str:
    if kind == "antigravity":
        return "agy"
    return kind


def build_service_config(workflow: WorkflowDefinition) -> ServiceConfig:
    """§6.1 — apply defaults and resolve typed values."""
    cfg = workflow.config
    base_dir = workflow.base_dir()

    tracker_raw = cfg.get("tracker") or {}
    if not isinstance(tracker_raw, dict):
        tracker_raw = {}

    tracker_kind = _as_str(tracker_raw.get("kind")).strip()
    endpoint_default = (
        LINEAR_DEFAULT_ENDPOINT if tracker_kind == "linear" else _as_str(tracker_raw.get("endpoint"))
    )
    tracker_endpoint = _as_str(tracker_raw.get("endpoint"), endpoint_default)
    raw_api_key = tracker_raw.get("api_key")
    if raw_api_key is None and tracker_kind == "linear":
        # Canonical env when literal not provided.
        raw_api_key = "$" + LINEAR_API_KEY_ENV
    if raw_api_key is None and tracker_kind == "jira":
        raw_api_key = "$" + JIRA_API_TOKEN_ENV
    tracker_api_key = _as_str(resolve_var_indirection(raw_api_key))

    raw_email = tracker_raw.get("email")
    if raw_email is None and tracker_kind == "jira":
        raw_email = "$" + JIRA_EMAIL_ENV
    tracker_email = _as_str(resolve_var_indirection(raw_email))

    tracker_project_slug = _as_str(resolve_var_indirection(tracker_raw.get("project_slug")))

    raw_board_root = tracker_raw.get("board_root")
    if isinstance(raw_board_root, str) and raw_board_root:
        resolved_board = (
            resolve_var_indirection(raw_board_root)
            if raw_board_root.startswith("$")
            else raw_board_root
        )
        if isinstance(resolved_board, str) and resolved_board:
            board_path = Path(expand_path_value(resolved_board))
            if not board_path.is_absolute():
                board_path = (base_dir / board_path).resolve()
            else:
                board_path = board_path.resolve()
        else:
            board_path = None
    else:
        board_path = (base_dir / DEFAULT_BOARD_ROOT_NAME).resolve() if tracker_kind == "file" else None

    archive_after_raw = tracker_raw.get("archive_after_days")
    if archive_after_raw is None:
        archive_after_days = 30
    elif isinstance(archive_after_raw, bool) or not isinstance(archive_after_raw, int):
        # Reject bools (which `int` accepts) and non-int types up front so
        # `archive_after_days: true` doesn't silently mean 1 day.
        raise ConfigValidationError(
            "tracker.archive_after_days must be a non-negative integer",
            value=archive_after_raw,
        )
    elif archive_after_raw < 0:
        raise ConfigValidationError(
            "tracker.archive_after_days must be a non-negative integer",
            value=archive_after_raw,
        )
    else:
        archive_after_days = archive_after_raw

    network_timeout_seconds = _validated_positive_float_or_default(
        tracker_raw.get("network_timeout_seconds"),
        30.0,
        name="tracker.network_timeout_seconds",
    )

    archive_state_raw = tracker_raw.get("archive_state")
    archive_state = (
        archive_state_raw.strip()
        if isinstance(archive_state_raw, str) and archive_state_raw.strip()
        else "Archive"
    )

    tracker = TrackerConfig(
        kind=tracker_kind,
        endpoint=tracker_endpoint,
        api_key=tracker_api_key,
        project_slug=tracker_project_slug,
        active_states=_as_str_list(tracker_raw.get("active_states"), DEFAULT_ACTIVE_STATES),
        terminal_states=_as_str_list(
            tracker_raw.get("terminal_states"), DEFAULT_TERMINAL_STATES
        ),
        board_root=board_path,
        state_descriptions=_normalize_state_description_map(
            tracker_raw.get("state_descriptions")
        ),
        archive_state=archive_state,
        archive_after_days=archive_after_days,
        email=tracker_email,
        network_timeout_seconds=network_timeout_seconds,
    )

    polling_raw = cfg.get("polling") or {}
    if not isinstance(polling_raw, dict):
        polling_raw = {}
    poll_interval_ms = _validated_positive_or_default(
        polling_raw.get("interval_ms"), DEFAULT_POLL_INTERVAL_MS, name="polling.interval_ms"
    )

    workspace_raw = cfg.get("workspace") or {}
    if not isinstance(workspace_raw, dict):
        workspace_raw = {}
    raw_root = workspace_raw.get("root")
    if isinstance(raw_root, str) and raw_root:
        # §5.3.3 — $VAR for env-backed path values, then ~ expansion.
        resolved = resolve_var_indirection(raw_root) if raw_root.startswith("$") else raw_root
        if isinstance(resolved, str) and resolved:
            workspace_root = Path(expand_path_value(resolved))
        else:
            workspace_root = Path(tempfile.gettempdir()) / "symphony_workspaces"
    else:
        workspace_root = Path(tempfile.gettempdir()) / "symphony_workspaces"

    if not workspace_root.is_absolute():
        workspace_root = (base_dir / workspace_root).resolve()
    else:
        workspace_root = workspace_root.resolve()
    workspace_reuse_policy = _as_str(
        workspace_raw.get("reuse_policy"), DEFAULT_WORKSPACE_REUSE_POLICY
    ).strip().lower() or DEFAULT_WORKSPACE_REUSE_POLICY
    if workspace_reuse_policy not in SUPPORTED_WORKSPACE_REUSE_POLICIES:
        raise ConfigValidationError(
            "workspace.reuse_policy must be one of "
            f"{sorted(SUPPORTED_WORKSPACE_REUSE_POLICIES)}",
            value=workspace_reuse_policy,
        )

    hooks_raw = cfg.get("hooks") or {}
    if not isinstance(hooks_raw, dict):
        hooks_raw = {}
    hooks = HooksConfig(
        after_create=hooks_raw.get("after_create") if isinstance(hooks_raw.get("after_create"), str) else None,
        before_run=hooks_raw.get("before_run") if isinstance(hooks_raw.get("before_run"), str) else None,
        after_run=hooks_raw.get("after_run") if isinstance(hooks_raw.get("after_run"), str) else None,
        before_remove=hooks_raw.get("before_remove") if isinstance(hooks_raw.get("before_remove"), str) else None,
        timeout_ms=_validated_positive_or_default(
            hooks_raw.get("timeout_ms"), DEFAULT_HOOK_TIMEOUT_MS, name="hooks.timeout_ms"
        ),
        after_done=hooks_raw.get("after_done") if isinstance(hooks_raw.get("after_done"), str) else None,
        fail_on_warning_patterns=bool(
            hooks_raw.get("fail_on_warning_patterns", False)
        ),
    )

    agent_raw = cfg.get("agent") or {}
    if not isinstance(agent_raw, dict):
        agent_raw = {}
    max_turns = _validated_positive_or_default(
        agent_raw.get("max_turns"), DEFAULT_MAX_TURNS, name="agent.max_turns"
    )
    max_total_turns = _validated_positive_or_default(
        agent_raw.get("max_total_turns"),
        DEFAULT_MAX_TOTAL_TURNS,
        name="agent.max_total_turns",
    )
    max_state_turns = _validated_nonnegative_or_default(
        agent_raw.get("max_state_turns"),
        DEFAULT_MAX_STATE_TURNS,
        name="agent.max_state_turns",
    )
    no_stage_change_action = _validated_no_stage_change_action(
        agent_raw.get("no_stage_change_action"),
        active_states=tracker.active_states,
        terminal_states=tracker.terminal_states,
    )
    agent_kind = _canonical_agent_kind(
        _as_str(agent_raw.get("kind"), DEFAULT_AGENT_KIND).strip().lower()
        or DEFAULT_AGENT_KIND
    )
    if agent_kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}",
            value=agent_kind,
        )
    agent = AgentConfig(
        kind=agent_kind,
        max_concurrent_agents=_validated_positive_or_default(
            agent_raw.get("max_concurrent_agents"),
            DEFAULT_MAX_CONCURRENT_AGENTS,
            name="agent.max_concurrent_agents",
        ),
        max_turns=max_turns,
        max_retry_backoff_ms=_validated_positive_or_default(
            agent_raw.get("max_retry_backoff_ms"),
            DEFAULT_MAX_RETRY_BACKOFF_MS,
            name="agent.max_retry_backoff_ms",
        ),
        max_concurrent_agents_by_state=_normalize_state_map(
            agent_raw.get("max_concurrent_agents_by_state")
        ),
        max_total_turns=max_total_turns,
        max_state_turns=max_state_turns,
        max_state_turns_by_state=_normalize_state_map(
            agent_raw.get("max_state_turns_by_state")
        ),
        no_stage_change_action=no_stage_change_action,
        max_attempts=_validated_nonnegative_or_default(
            agent_raw.get("max_attempts"),
            DEFAULT_MAX_ATTEMPTS,
            name="agent.max_attempts",
        ),
        max_retries=_validated_nonnegative_or_default(
            agent_raw.get("max_retries"),
            DEFAULT_MAX_RETRIES,
            name="agent.max_retries",
        ),
        auto_triage_actionable_todo=bool(
            agent_raw.get("auto_triage_actionable_todo", True)
        ),
        auto_recover_blocked=bool(
            agent_raw.get("auto_recover_blocked", DEFAULT_AUTO_RECOVER_BLOCKED)
        ),
        compact_issue_context=bool(
            agent_raw.get("compact_issue_context", True)
        ),
        crash_continuation=_validated_bool(
            agent_raw.get("crash_continuation"),
            True,
            name="agent.crash_continuation",
        ),
        scheduling_policy=_validated_scheduling_policy(
            agent_raw.get("scheduling_policy")
        ),
        auto_commit_on_done=bool(
            agent_raw.get("auto_commit_on_done", True)
        ),
        auto_merge_on_done=bool(
            agent_raw.get("auto_merge_on_done", True)
        ),
        auto_merge_push_target=bool(
            agent_raw.get("auto_merge_push_target", True)
        ),
        auto_merge_target_branch=_as_str(
            agent_raw.get("auto_merge_target_branch"), ""
        ) or "",
        feature_base_branch=_as_str(
            agent_raw.get("feature_base_branch"), ""
        ) or "",
        auto_merge_exclude_paths=_as_str_list(
            agent_raw.get("auto_merge_exclude_paths"),
            DEFAULT_AUTO_MERGE_EXCLUDE_PATHS,
        ),
        auto_merge_capture_untracked=_as_str_list(
            agent_raw.get("auto_merge_capture_untracked"),
            (),
        ),
        after_done_failure_policy=_validated_after_done_failure_policy(
            agent_raw.get("after_done_failure_policy"),
        ),
        max_total_tokens=_validated_nonnegative_or_default(
            agent_raw.get("max_total_tokens"),
            0,
            name="agent.max_total_tokens",
        ),
        max_total_tokens_by_state=_normalize_state_map(
            agent_raw.get("max_total_tokens_by_state")
        ),
        token_attention_threshold_by_state=_normalize_state_map(
            agent_raw.get("token_attention_threshold_by_state")
        ),
        budget_exhausted_state=_as_str(
            agent_raw.get("budget_exhausted_state"), ""
        ) or "",
        stage_kinds=_validated_stage_kinds(
            agent_raw.get("stage_kinds"),
            active_states=tracker.active_states,
            terminal_states=tracker.terminal_states,
        ),
        stage_contracts=_validated_stage_contracts(
            agent_raw.get("stage_contracts")
        ),
        stall_timeout_ms_by_state=_validated_stall_timeout_by_state(
            agent_raw.get("stall_timeout_ms_by_state"),
            active_states=tracker.active_states,
            terminal_states=tracker.terminal_states,
        ),
    )

    codex_raw = cfg.get("codex") or {}
    if not isinstance(codex_raw, dict):
        codex_raw = {}
    codex = CodexConfig(
        command=_as_str(codex_raw.get("command"), DEFAULT_CODEX_COMMAND) or DEFAULT_CODEX_COMMAND,
        approval_policy=codex_raw.get("approval_policy"),
        thread_sandbox=codex_raw.get("thread_sandbox"),
        turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
        turn_timeout_ms=_validated_positive_or_default(
            codex_raw.get("turn_timeout_ms"), DEFAULT_CODEX_TURN_TIMEOUT_MS, name="codex.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            codex_raw.get("read_timeout_ms"), DEFAULT_CODEX_READ_TIMEOUT_MS, name="codex.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            codex_raw.get("stall_timeout_ms"), DEFAULT_CODEX_STALL_TIMEOUT_MS, name="codex.stall_timeout_ms"
        ),
        model=_as_str(codex_raw.get("model"), DEFAULT_CODEX_MODEL) or DEFAULT_CODEX_MODEL,
        reasoning_effort=_as_str(
            codex_raw.get("reasoning_effort"), DEFAULT_CODEX_REASONING_EFFORT
        ) or DEFAULT_CODEX_REASONING_EFFORT,
    )

    claude_raw = cfg.get("claude") or {}
    if not isinstance(claude_raw, dict):
        claude_raw = {}
    claude = ClaudeConfig(
        command=_as_str(claude_raw.get("command"), DEFAULT_CLAUDE_COMMAND) or DEFAULT_CLAUDE_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            claude_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="claude.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            claude_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="claude.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            claude_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="claude.stall_timeout_ms"
        ),
        resume_across_turns=bool(claude_raw.get("resume_across_turns", True)),
    )

    gemini_raw = cfg.get("gemini") or {}
    if not isinstance(gemini_raw, dict):
        gemini_raw = {}
    gemini = GeminiConfig(
        command=_as_str(gemini_raw.get("command"), DEFAULT_GEMINI_COMMAND) or DEFAULT_GEMINI_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="gemini.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="gemini.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="gemini.stall_timeout_ms"
        ),
        resume_across_turns=bool(gemini_raw.get("resume_across_turns", True)),
    )

    agy_raw = cfg.get("agy") or cfg.get("antigravity") or {}
    if not isinstance(agy_raw, dict):
        agy_raw = {}
    agy = AgyConfig(
        command=_as_str(agy_raw.get("command"), DEFAULT_AGY_COMMAND) or DEFAULT_AGY_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            agy_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="agy.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            agy_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="agy.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            agy_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="agy.stall_timeout_ms"
        ),
        resume_across_turns=bool(agy_raw.get("resume_across_turns", False)),
    )

    kiro_raw = cfg.get("kiro") or {}
    if not isinstance(kiro_raw, dict):
        kiro_raw = {}
    kiro = KiroConfig(
        command=_as_str(kiro_raw.get("command"), DEFAULT_KIRO_COMMAND) or DEFAULT_KIRO_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            kiro_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="kiro.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            kiro_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="kiro.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            kiro_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="kiro.stall_timeout_ms"
        ),
        resume_across_turns=bool(kiro_raw.get("resume_across_turns", True)),
    )

    opencode_raw = cfg.get("opencode") or {}
    if not isinstance(opencode_raw, dict):
        opencode_raw = {}
    opencode = OpenCodeConfig(
        command=_as_str(opencode_raw.get("command"), DEFAULT_OPENCODE_COMMAND) or DEFAULT_OPENCODE_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            opencode_raw.get("turn_timeout_ms"),
            DEFAULT_BACKEND_TURN_TIMEOUT_MS,
            name="opencode.turn_timeout_ms",
        ),
        read_timeout_ms=_validated_positive_or_default(
            opencode_raw.get("read_timeout_ms"),
            DEFAULT_BACKEND_READ_TIMEOUT_MS,
            name="opencode.read_timeout_ms",
        ),
        stall_timeout_ms=_validated_positive_or_default(
            opencode_raw.get("stall_timeout_ms"),
            DEFAULT_BACKEND_STALL_TIMEOUT_MS,
            name="opencode.stall_timeout_ms",
        ),
        resume_across_turns=bool(opencode_raw.get("resume_across_turns", True)),
    )

    pi_raw = cfg.get("pi") or {}
    if not isinstance(pi_raw, dict):
        pi_raw = {}
    pi = PiConfig(
        command=_as_str(pi_raw.get("command"), DEFAULT_PI_COMMAND) or DEFAULT_PI_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            pi_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="pi.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            pi_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="pi.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            pi_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="pi.stall_timeout_ms"
        ),
        resume_across_turns=bool(pi_raw.get("resume_across_turns", True)),
    )

    pa_raw = cfg.get("prime_agent") or {}
    if not isinstance(pa_raw, dict):
        pa_raw = {}
    prime_agent = PrimeAgentConfig(
        command=_as_str(pa_raw.get("command"), DEFAULT_PRIME_AGENT_COMMAND)
        or DEFAULT_PRIME_AGENT_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            pa_raw.get("turn_timeout_ms"),
            DEFAULT_BACKEND_TURN_TIMEOUT_MS,
            name="prime_agent.turn_timeout_ms",
        ),
        read_timeout_ms=_validated_positive_or_default(
            pa_raw.get("read_timeout_ms"),
            DEFAULT_BACKEND_READ_TIMEOUT_MS,
            name="prime_agent.read_timeout_ms",
        ),
        stall_timeout_ms=_validated_positive_or_default(
            pa_raw.get("stall_timeout_ms"),
            DEFAULT_BACKEND_STALL_TIMEOUT_MS,
            name="prime_agent.stall_timeout_ms",
        ),
        resume_across_turns=bool(pa_raw.get("resume_across_turns", True)),
    )

    server_raw = cfg.get("server") or {}
    if not isinstance(server_raw, dict):
        server_raw = {}
    raw_port = server_raw.get("port")
    if isinstance(raw_port, bool):
        port = None
    elif isinstance(raw_port, int):
        port = raw_port
    else:
        port = None
    server = ServerConfig(port=port)

    preview_raw = cfg.get("preview") or {}
    if not isinstance(preview_raw, dict):
        raise ConfigValidationError("preview must be a mapping", value=preview_raw)
    preview_command = _as_str(preview_raw.get("command"), "").strip()
    # A launch recipe is usable by default.  Missing preview configuration
    # remains disabled/unconfigured, while operators can still opt out of a
    # checked-in recipe explicitly with ``enabled: false``.
    preview_enabled = _validated_bool(
        preview_raw.get("enabled"), bool(preview_command), name="preview.enabled"
    )
    preview_cwd = _as_str(preview_raw.get("cwd"), ".").strip() or "."
    # Reject escapes under both POSIX and native path semantics. The
    # workflow config's path space is POSIX-flavored: on Windows a drive-less
    # `Path("/tmp/outside")` reports `is_absolute() == False`, which would
    # let a leading-slash escape through, while a drive-absolute Windows path
    # is only caught by the native check.
    cwd_posix = PurePosixPath(preview_cwd)
    cwd_native = Path(preview_cwd)
    if cwd_posix.is_absolute() or cwd_native.is_absolute() or any(
        part == ".." for part in (*cwd_posix.parts, *cwd_native.parts)
    ):
        raise ConfigValidationError(
            "preview.cwd must be a relative path inside the preview checkout",
            value=preview_cwd,
        )
    health_path = _as_str(preview_raw.get("health_path"), "/").strip() or "/"
    url_path = _as_str(preview_raw.get("url_path"), "/").strip() or "/"
    for name, path_value in (("preview.health_path", health_path), ("preview.url_path", url_path)):
        if not path_value.startswith("/") or "://" in path_value:
            raise ConfigValidationError(
                f"{name} must be an absolute URL path", value=path_value
            )
    startup_timeout_ms = _validated_positive_or_default(
        preview_raw.get("startup_timeout_ms"),
        30_000,
        name="preview.startup_timeout_ms",
    )
    release_ticket = _as_str(preview_raw.get("release_ticket"), "").strip()
    acceptance_raw = preview_raw.get("acceptance", [])
    if not isinstance(acceptance_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in acceptance_raw
    ):
        raise ConfigValidationError(
            "preview.acceptance must be a list of non-empty strings",
            value=acceptance_raw,
        )
    if preview_enabled and not preview_command:
        raise ConfigValidationError(
            "preview.command is required when preview.enabled is true"
        )
    if preview_enabled and "${HOST}" not in preview_command:
        raise ConfigValidationError(
            "preview.command must include ${HOST} so the product binds to loopback"
        )
    preview = PreviewConfig(
        enabled=preview_enabled,
        command=preview_command,
        cwd=preview_cwd,
        health_path=health_path,
        url_path=url_path,
        startup_timeout_ms=startup_timeout_ms,
        release_ticket=release_ticket,
        acceptance=tuple(item.strip() for item in acceptance_raw),
    )

    artifacts_raw = cfg.get("artifacts") or {}
    if not isinstance(artifacts_raw, dict):
        raise ConfigValidationError("artifacts must be a mapping", value=artifacts_raw)
    artifacts_defaults = ArtifactsConfig()
    artifacts_dir = (
        _as_str(artifacts_raw.get("dir"), artifacts_defaults.dir).strip()
        or artifacts_defaults.dir
    )
    dir_path = Path(artifacts_dir)
    if (
        dir_path.is_absolute()
        or len(dir_path.parts) != 1
        or artifacts_dir in (".", "..")
    ):
        raise ConfigValidationError(
            "artifacts.dir must be a single relative directory name",
            value=artifacts_dir,
        )
    # The name is written verbatim into `.git/info/exclude`. Gitignore
    # metacharacters would change what the line means -- `#drop` is a
    # comment, `!keep` a negation -- and the rule would silently stop
    # protecting the branch from collected deliverables.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", artifacts_dir):
        raise ConfigValidationError(
            "artifacts.dir may contain only letters, digits, dot, dash and "
            "underscore (it is written into .git/info/exclude verbatim)",
            value=artifacts_dir,
        )
    artifacts_ttl_raw = artifacts_raw.get("ttl_days")
    if artifacts_ttl_raw is None:
        artifacts_ttl_days = artifacts_defaults.ttl_days
    elif (
        isinstance(artifacts_ttl_raw, bool)
        or not isinstance(artifacts_ttl_raw, int)
        or artifacts_ttl_raw < 0
    ):
        raise ConfigValidationError(
            "artifacts.ttl_days must be an integer >= 0 (0 disables the sweep)",
            value=artifacts_ttl_raw,
        )
    else:
        artifacts_ttl_days = artifacts_ttl_raw
    artifacts = ArtifactsConfig(
        enabled=_validated_bool(
            artifacts_raw.get("enabled"), True, name="artifacts.enabled"
        ),
        dir=artifacts_dir,
        max_file_mb=_validated_positive_or_default(
            artifacts_raw.get("max_file_mb"),
            artifacts_defaults.max_file_mb,
            name="artifacts.max_file_mb",
        ),
        max_ticket_mb=_validated_positive_or_default(
            artifacts_raw.get("max_ticket_mb"),
            artifacts_defaults.max_ticket_mb,
            name="artifacts.max_ticket_mb",
        ),
        ttl_days=artifacts_ttl_days,
        require_for_done=_validated_bool(
            artifacts_raw.get("require_for_done"),
            False,
            name="artifacts.require_for_done",
        ),
    )

    tui_raw = cfg.get("tui") or {}
    if not isinstance(tui_raw, dict):
        tui_raw = {}
    # Lazy import to avoid a circular dep cycle: i18n is allowed to read
    # workflow constants in the future without us bootstrapping it eagerly.
    from ..i18n import resolve_language
    # SYMPHONY_LANG env var takes precedence over WORKFLOW.md so a single
    # operator can flip without editing the shared workflow file.
    # `_as_int(..., allow_zero=False)` rejects 0/negative as invalid → falls
    # back to default 5. Belt-and-suspenders `max(1, ...)` covers the case
    # where a user sets `visible_lanes: 0` deliberately and the helper still
    # returns it through the allow_zero path elsewhere.
    visible_lanes = max(1, _as_int(tui_raw.get("visible_lanes"), 5, allow_zero=False))
    tui = TuiConfig(
        language=resolve_language(tui_raw.get("language")),
        visible_lanes=visible_lanes,
    )

    progress_raw = cfg.get("progress") or {}
    if not isinstance(progress_raw, dict):
        progress_raw = {}
    raw_enabled = progress_raw.get("enabled", True)
    if isinstance(raw_enabled, bool):
        progress_enabled = raw_enabled
    else:
        # Mirror archive_after_days: refuse silent coercions of 0/1/"true".
        raise ConfigValidationError(
            "progress.enabled must be a boolean", value=raw_enabled
        )
    raw_path = progress_raw.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        resolved_path = (
            resolve_var_indirection(raw_path) if raw_path.startswith("$") else raw_path
        )
        candidate = Path(expand_path_value(str(resolved_path)))
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        progress_path: Path | None = candidate
    else:
        progress_path = (base_dir / "WORKFLOW-PROGRESS.md").resolve()
    raw_max_transitions = progress_raw.get("max_transitions")
    if raw_max_transitions is None:
        max_transitions = 20
    elif isinstance(raw_max_transitions, bool) or not isinstance(raw_max_transitions, int):
        raise ConfigValidationError(
            "progress.max_transitions must be a non-negative integer",
            value=raw_max_transitions,
        )
    elif raw_max_transitions < 0:
        raise ConfigValidationError(
            "progress.max_transitions must be a non-negative integer",
            value=raw_max_transitions,
        )
    else:
        max_transitions = raw_max_transitions
    progress = ProgressConfig(
        enabled=progress_enabled,
        path=progress_path,
        max_transitions=max_transitions,
    )

    system_raw = cfg.get("system") or {}
    if not isinstance(system_raw, dict):
        system_raw = {}
    raw_keep_awake = system_raw.get("keep_awake", True)
    if isinstance(raw_keep_awake, bool):
        keep_awake = raw_keep_awake
    else:
        raise ConfigValidationError(
            "system.keep_awake must be a boolean", value=raw_keep_awake
        )
    system = SystemConfig(keep_awake=keep_awake)

    prompt_template = workflow.prompt_template or DEFAULT_PROMPT
    prompts = _build_prompt_config(cfg.get("prompts"), base_dir)

    wiki_raw = cfg.get("wiki") or {}
    if not isinstance(wiki_raw, dict):
        wiki_raw = {}
    sweep_every_n = _validated_nonnegative_or_default(
        wiki_raw.get("sweep_every_n"), 10, name="wiki.sweep_every_n"
    )
    raw_wiki_root = wiki_raw.get("root")
    if isinstance(raw_wiki_root, str) and raw_wiki_root.strip():
        resolved_wiki = (
            resolve_var_indirection(raw_wiki_root)
            if raw_wiki_root.startswith("$")
            else raw_wiki_root
        )
        if isinstance(resolved_wiki, str) and resolved_wiki:
            wiki_path = Path(expand_path_value(resolved_wiki))
            if not wiki_path.is_absolute():
                wiki_path = (base_dir / wiki_path).resolve()
            else:
                wiki_path = wiki_path.resolve()
        else:
            wiki_path = (base_dir / "docs" / "llm-wiki").resolve()
    else:
        wiki_path = (base_dir / "docs" / "llm-wiki").resolve()
    wiki = WikiConfig(sweep_every_n=sweep_every_n, root=wiki_path)

    notifications = build_notifications_config(
        cfg.get("notifications"),
        resolve_var=resolve_var_indirection,
    )

    continuous_improvement = _build_continuous_improvement_config(
        cfg.get("continuous_improvement")
    )

    _log_stage_contracts_decision(agent, tracker)

    return ServiceConfig(
        workflow_path=workflow.source_path,
        poll_interval_ms=poll_interval_ms,
        workspace_root=workspace_root,
        tracker=tracker,
        hooks=hooks,
        agent=agent,
        codex=codex,
        claude=claude,
        gemini=gemini,
        agy=agy,
        kiro=kiro,
        opencode=opencode,
        pi=pi,
        prime_agent=prime_agent,
        server=server,
        tui=tui,
        progress=progress,
        system=system,
        prompts=prompts,
        wiki=wiki,
        notifications=notifications,
        continuous_improvement=continuous_improvement,
        raw=dict(cfg),
        prompt_template=prompt_template,
        workspace_reuse_policy=workspace_reuse_policy,
        preview=preview,
        artifacts=artifacts,
    )


def _validated_positive_or_default(value: Any, default: int, *, name: str) -> int:
    """§5.3.4, §5.3.5 — invalid values fail validation."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a positive integer", value=value)
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(
            f"{name} must be a positive integer", value=value
        ) from exc
    if ivalue <= 0:
        raise ConfigValidationError(f"{name} must be a positive integer", value=value)
    return ivalue


def _validated_positive_float_or_default(
    value: Any, default: float, *, name: str
) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a positive number", value=value)
    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(
            f"{name} must be a positive number", value=value
        ) from exc
    if fvalue <= 0:
        raise ConfigValidationError(f"{name} must be a positive number", value=value)
    return fvalue


def _validated_nonnegative_or_default(value: Any, default: int, *, name: str) -> int:
    """Validate counters where 0 is an explicit off switch."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a non-negative integer", value=value)
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(
            f"{name} must be a non-negative integer", value=value
        ) from exc
    if ivalue < 0:
        raise ConfigValidationError(f"{name} must be a non-negative integer", value=value)
    return ivalue


def _validated_stage_kinds(
    value: Any,
    *,
    active_states: tuple[str, ...],
    terminal_states: tuple[str, ...],
) -> dict[str, str]:
    """agent.stage_kinds — per-state backend routing, keys lowercased.

    Values must be supported agent kinds (a typo would dispatch nothing, so
    it is a hard config error). Unknown state keys only warn: states are
    user-editable through the web UI, and a stale mapping entry should not
    brick the whole workflow load.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigValidationError(
            "agent.stage_kinds must be a map of state name to agent kind",
            value=value,
        )
    known = {state.strip().lower() for state in (*active_states, *terminal_states)}
    out: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip().lower()
        if not normalized_key:
            continue
        kind = _canonical_agent_kind(_as_str(raw).strip().lower())
        if kind not in SUPPORTED_AGENT_KINDS:
            raise ConfigValidationError(
                f"agent.stage_kinds[{key!r}] must be one of "
                f"{sorted(SUPPORTED_AGENT_KINDS)}",
                value=raw,
            )
        if normalized_key not in known:
            get_logger().warning(
                "agent_stage_kinds_unknown_state",
                state=key,
                known_states=sorted(known),
            )
        out[normalized_key] = kind
    return out


def _log_stage_contracts_decision(agent: AgentConfig, tracker: TrackerConfig) -> None:
    """Say out loud when the mechanical evidence floor is NOT running.

    F-06: renaming one default lane (`Document` → `Docs`) silently switched
    the whole stage-contract validator off. It is a legitimate outcome of a
    customized board, but it must never be invisible — this is the product's
    evidence floor.
    """
    mode = (agent.stage_contracts or "auto").strip().lower()
    if agent.stage_contracts_enabled(tracker.active_states):
        return
    if mode == "off":
        get_logger().info(
            "stage_contracts_disabled",
            reason="agent.stage_contracts: off",
            lanes=list(tracker.active_states),
        )
        return
    offending = [
        state
        for state in tracker.active_states
        if not board_uses_default_contracts((state,))
    ]
    get_logger().warning(
        "stage_contracts_disabled",
        reason="board lanes are not the default preset",
        offending_lanes=offending,
        lanes=list(tracker.active_states),
        hint="set agent.stage_contracts: on to enforce them anyway",
    )


_STAGE_CONTRACT_MODES = ("auto", "on", "off")


def _validated_stage_contracts(value: Any) -> str:
    """agent.stage_contracts — auto (default) | on | off."""
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "on" if value else "off"
    mode = _as_str(value).strip().lower()
    if mode not in _STAGE_CONTRACT_MODES:
        raise ConfigValidationError(
            f"agent.stage_contracts must be one of {list(_STAGE_CONTRACT_MODES)}",
            value=value,
        )
    return mode


def _validated_stall_timeout_by_state(
    value: Any,
    *,
    active_states: tuple[str, ...],
    terminal_states: tuple[str, ...],
) -> dict[str, int]:
    """agent.stall_timeout_ms_by_state — per-lane stall budget, keys lowercased.

    Same shape as `max_total_tokens_by_state`: non-positive / non-numeric
    entries are dropped rather than fatal, because a stall budget is an
    ergonomics knob and a stale entry must not brick the workflow load.
    Unknown state keys warn (states are UI-editable).
    """
    if value is not None and not isinstance(value, dict):
        raise ConfigValidationError(
            "agent.stall_timeout_ms_by_state must be a map of state name to ms",
            value=value,
        )
    out = _normalize_state_map(value)
    known = {state.strip().lower() for state in (*active_states, *terminal_states)}
    for key in out:
        if key not in known:
            get_logger().warning(
                "agent_stall_timeout_unknown_state",
                state=key,
                known_states=sorted(known),
            )
    return out


def _validated_scheduling_policy(value: Any) -> str:
    if value is None:
        return "fifo"
    if not isinstance(value, str) or value.strip().lower() not in {"fifo", "dag"}:
        raise ConfigValidationError(
            "agent.scheduling_policy must be one of ['fifo', 'dag']",
            value=value,
        )
    return value.strip().lower()


def _validated_after_done_failure_policy(value: Any) -> str:
    """Accept 'warn' (default) or 'block'. Anything else is a config error."""
    if value is None:
        return "warn"
    if not isinstance(value, str) or value not in _AFTER_DONE_FAILURE_POLICIES:
        raise ConfigValidationError(
            "agent.after_done_failure_policy must be one of "
            f"{list(_AFTER_DONE_FAILURE_POLICIES)}",
            value=value,
        )
    return value


def _validated_no_stage_change_action(
    value: Any,
    *,
    active_states: tuple[str, ...],
    terminal_states: tuple[str, ...],
) -> str:
    action = _as_str(value, "block").strip() or "block"
    if action.lower() == "block":
        return "block"
    configured = {
        state.strip().lower(): state.strip()
        for state in (*active_states, *terminal_states)
        if state.strip()
    }
    key = action.lower()
    if key not in configured:
        raise ConfigValidationError(
            "agent.no_stage_change_action must be 'block' or a configured tracker state",
            value=action,
        )
    return configured[key]


def _validated_bool(value: Any, default: bool, *, name: str) -> bool:
    """Strict boolean — YAML `1`/`"true"`/`"false"` are config errors, not truthy coercions."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a boolean", value=value)
    return value


def _validated_strict_int(
    value: Any, default: int, *, name: str, minimum: int
) -> int:
    """Stricter than `_validated_positive_or_default`: rejects numeric strings too.

    Only a real `int` (never `bool`) is accepted — `"1800000"` is a config
    error here, unlike the legacy `_validated_*_or_default` family which
    happily coerces any int-parseable string.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{name} must be an integer", value=value)
    if value < minimum:
        raise ConfigValidationError(f"{name} must be >= {minimum}", value=value)
    return value


def _validated_ci_agent_kind(value: Any) -> str:
    """Empty string (inherit workflow default agent) or a member of SUPPORTED_AGENT_KINDS.

    Mirrors `webapi._check_agent_kind`'s normalize-then-validate shape.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigValidationError(
            "continuous_improvement.agent_kind must be a string", value=value
        )
    kind = value.strip().lower()
    if kind and kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            "continuous_improvement.agent_kind must be \"\" or one of "
            f"{sorted(SUPPORTED_AGENT_KINDS)}",
            value=kind,
        )
    return kind


# Sole consumer is _validated_ci_ticket_prefix below — kept local rather than
# in constants.py since nothing else in the package needs it.
_CI_TICKET_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,19}$")


def _validated_ci_ticket_prefix(value: Any) -> str:
    if value is None:
        return DEFAULT_CI_TICKET_PREFIX
    if not isinstance(value, str) or not _CI_TICKET_PREFIX_RE.match(value):
        raise ConfigValidationError(
            "continuous_improvement.ticket_prefix must be an identifier-safe string "
            "(letters/digits, starting with a letter, max 20 chars)",
            value=value,
        )
    return value



def validated_ci_modes(value: Any) -> tuple[str, ...]:
    """Normalize `continuous_improvement.modes` to canonical mode order.

    `None`/absent means "no explicit modes" — the config's `resolved_modes()`
    then falls back to readiness-only, which is what an `enabled: true` block
    meant before improvement modes existed. Shared with the mutation API so
    WORKFLOW.md and the settings card reject the same strings.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConfigValidationError(
            "continuous_improvement.modes must be a list of mode names",
            value=value,
        )
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigValidationError(
                "continuous_improvement.modes must be a list of mode names",
                value=item,
            )
        mode = item.strip().lower()
        if mode not in SUPPORTED_CI_MODES:
            raise ConfigValidationError(
                "unknown continuous_improvement mode "
                f"{mode!r}; supported: {list(SUPPORTED_CI_MODES)}",
                value=item,
            )
        if mode not in seen:
            seen.append(mode)
    return tuple(mode for mode in SUPPORTED_CI_MODES if mode in seen)


def _validated_ci_mode_interval_hours(value: Any) -> dict[str, float]:
    """`{mode: hours}` cadence overrides; hours must be a non-negative number."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigValidationError(
            "continuous_improvement.mode_interval_hours must be a mapping "
            "of mode name to hours",
            value=value,
        )
    out: dict[str, float] = {}
    for raw_mode, raw_hours in value.items():
        mode = raw_mode.strip().lower() if isinstance(raw_mode, str) else raw_mode
        if mode not in SUPPORTED_CI_MODES:
            raise ConfigValidationError(
                "unknown continuous_improvement mode "
                f"{raw_mode!r}; supported: {list(SUPPORTED_CI_MODES)}",
                value=raw_mode,
            )
        if isinstance(raw_hours, bool) or not isinstance(raw_hours, (int, float)):
            raise ConfigValidationError(
                f"continuous_improvement.mode_interval_hours.{mode} must be a number",
                value=raw_hours,
            )
        if raw_hours < 0:
            raise ConfigValidationError(
                f"continuous_improvement.mode_interval_hours.{mode} must be >= 0",
                value=raw_hours,
            )
        out[mode] = float(raw_hours)
    return out


def _build_continuous_improvement_config(raw: Any) -> ContinuousImprovementConfig:
    """§continuous-improvement heartbeat — default-off, all fields validated strictly.

    Missing `continuous_improvement:` in WORKFLOW.md means disabled with
    defaults (see docs/continuous-improvement/rubric.md "Default configuration").
    """
    ci_raw = raw or {}
    if not isinstance(ci_raw, dict):
        ci_raw = {}

    enabled = _validated_bool(
        ci_raw.get("enabled"), False, name="continuous_improvement.enabled"
    )
    interval_ms = _validated_strict_int(
        ci_raw.get("interval_ms"),
        DEFAULT_CI_INTERVAL_MS,
        name="continuous_improvement.interval_ms",
        minimum=DEFAULT_CI_MIN_INTERVAL_MS,
    )
    max_turns = _validated_strict_int(
        ci_raw.get("max_turns"),
        DEFAULT_CI_MAX_TURNS,
        name="continuous_improvement.max_turns",
        minimum=0,
    )
    ticket_prefix = _validated_ci_ticket_prefix(ci_raw.get("ticket_prefix"))
    max_tickets_per_run = _validated_strict_int(
        ci_raw.get("max_tickets_per_run"),
        DEFAULT_CI_MAX_TICKETS_PER_RUN,
        name="continuous_improvement.max_tickets_per_run",
        minimum=1,
    )
    require_idle_board = _validated_bool(
        ci_raw.get("require_idle_board"),
        True,
        name="continuous_improvement.require_idle_board",
    )
    agent_kind = _validated_ci_agent_kind(ci_raw.get("agent_kind"))
    modes = validated_ci_modes(ci_raw.get("modes"))
    mode_interval_hours = _validated_ci_mode_interval_hours(
        ci_raw.get("mode_interval_hours")
    )
    max_improvement_tickets_per_run = _validated_strict_int(
        ci_raw.get("max_improvement_tickets_per_run"),
        DEFAULT_CI_MAX_IMPROVEMENT_TICKETS_PER_RUN,
        name="continuous_improvement.max_improvement_tickets_per_run",
        minimum=1,
    )

    return ContinuousImprovementConfig(
        enabled=enabled,
        interval_ms=interval_ms,
        max_turns=max_turns,
        ticket_prefix=ticket_prefix,
        max_tickets_per_run=max_tickets_per_run,
        require_idle_board=require_idle_board,
        agent_kind=agent_kind,
        modes=modes,
        mode_interval_hours=mode_interval_hours,
        max_improvement_tickets_per_run=max_improvement_tickets_per_run,
    )
