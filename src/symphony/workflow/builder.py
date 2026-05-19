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

import tempfile
from pathlib import Path
from typing import Any

from ..errors import ConfigValidationError
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
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
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
    DEFAULT_AGENT_KIND,
    DEFAULT_AUTO_MERGE_EXCLUDE_PATHS,
    DEFAULT_BACKEND_READ_TIMEOUT_MS,
    DEFAULT_BACKEND_STALL_TIMEOUT_MS,
    DEFAULT_BACKEND_TURN_TIMEOUT_MS,
    DEFAULT_BOARD_ROOT_NAME,
    DEFAULT_CLAUDE_COMMAND,
    DEFAULT_CODEX_COMMAND,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_READ_TIMEOUT_MS,
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_CODEX_STALL_TIMEOUT_MS,
    DEFAULT_CODEX_TURN_TIMEOUT_MS,
    DEFAULT_GEMINI_COMMAND,
    DEFAULT_HOOK_TIMEOUT_MS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_CONCURRENT_AGENTS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_RETRY_BACKOFF_MS,
    DEFAULT_MAX_TOTAL_TURNS,
    DEFAULT_MAX_TURNS,
    DEFAULT_PI_COMMAND,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_PROMPT,
    DEFAULT_TERMINAL_STATES,
    DEFAULT_WORKSPACE_REUSE_POLICY,
    JIRA_API_TOKEN_ENV,
    JIRA_EMAIL_ENV,
    LINEAR_API_KEY_ENV,
    LINEAR_DEFAULT_ENDPOINT,
    SUPPORTED_AGENT_KINDS,
    SUPPORTED_WORKSPACE_REUSE_POLICIES,
)
from .parser import WorkflowDefinition


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
    agent_kind = _as_str(agent_raw.get("kind"), DEFAULT_AGENT_KIND).strip().lower() or DEFAULT_AGENT_KIND
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
        auto_commit_on_done=bool(
            agent_raw.get("auto_commit_on_done", True)
        ),
        auto_merge_on_done=bool(
            agent_raw.get("auto_merge_on_done", True)
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
        budget_exhausted_state=_as_str(
            agent_raw.get("budget_exhausted_state"), ""
        ) or "",
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
        pi=pi,
        server=server,
        tui=tui,
        progress=progress,
        system=system,
        prompts=prompts,
        wiki=wiki,
        notifications=notifications,
        raw=dict(cfg),
        prompt_template=prompt_template,
        workspace_reuse_policy=workspace_reuse_policy,
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
