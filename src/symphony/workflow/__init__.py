"""SPEC §5, §6 — WORKFLOW.md loader, parser, typed config view.

Public surface intentionally preserves every name that used to live in
the flat `symphony/workflow.py`. Test fixtures and other modules import
straight from `symphony.workflow.X`, and `monkeypatch.setattr` on those
dotted paths must keep targeting the live function rather than a stale
copy.
"""

from __future__ import annotations

# Re-exports — order chosen so a reader can scan the public surface
# (parser → coercion → config → builder → preflight → state) the same
# way the modules depend on each other.

from .constants import (
    _AFTER_DONE_FAILURE_POLICIES,
    _VAR_PATTERN,
    DEFAULT_ACTIVE_STATES,
    DEFAULT_AGY_COMMAND,
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
    CI_AGENT_MODES,
    CI_MODE_BLOCKED_FIXES,
    CI_MODE_FEATURE_IMPROVEMENTS,
    CI_MODE_MARKET_RESEARCH,
    CI_MODE_READINESS,
    CI_MODE_SECURITY,
    DEFAULT_CI_INTERVAL_MS,
    DEFAULT_CI_MAX_IMPROVEMENT_TICKETS_PER_RUN,
    DEFAULT_CI_MAX_TICKETS_PER_RUN,
    DEFAULT_CI_MAX_TURNS,
    DEFAULT_CI_MIN_INTERVAL_MS,
    DEFAULT_CI_MODE_INTERVAL_HOURS,
    DEFAULT_CI_TICKET_PREFIX,
    DEFAULT_GEMINI_COMMAND,
    DEFAULT_HOOK_TIMEOUT_MS,
    DEFAULT_KIRO_COMMAND,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_CONCURRENT_AGENTS,
    DEFAULT_MAX_REOPENS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_RETRY_BACKOFF_MS,
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
    SUPPORTED_TRACKER_KINDS,
    SUPPORTED_WORKSPACE_REUSE_POLICIES,
    SYMPHONY_BRANCH_PREFIX,
)
from .parser import (
    WorkflowDefinition,
    load_workflow,
    parse_workflow_text,
    resolve_workflow_path,
)
from .coercion import (
    _as_int,
    _as_str,
    _as_str_list,
    _normalize_state_description_map,
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
from .builder import build_service_config, validated_ci_modes
from .preflight import validate_for_dispatch
from .state import WorkflowState

__all__ = [
    # parser
    "WorkflowDefinition",
    "load_workflow",
    "parse_workflow_text",
    "resolve_workflow_path",
    # coercion
    "resolve_var_indirection",
    "expand_path_value",
    # config
    "TrackerConfig",
    "HooksConfig",
    "AgentConfig",
    "AgyConfig",
    "CodexConfig",
    "ClaudeConfig",
    "GeminiConfig",
    "KiroConfig",
    "OpenCodeConfig",
    "PiConfig",
    "PrimeAgentConfig",
    "PreviewConfig",
    "ServerConfig",
    "TuiConfig",
    "ProgressConfig",
    "SystemConfig",
    "WikiConfig",
    "PromptConfig",
    "ServiceConfig",
    "ContinuousImprovementConfig",
    # builder
    "build_service_config",
    "validated_ci_modes",
    # preflight
    "validate_for_dispatch",
    # state
    "WorkflowState",
    # constants used by callers / tests
    "SUPPORTED_AGENT_KINDS",
    "SUPPORTED_CI_MODES",
    "CI_AGENT_MODES",
    "CI_MODE_READINESS",
    "CI_MODE_BLOCKED_FIXES",
    "CI_MODE_SECURITY",
    "CI_MODE_MARKET_RESEARCH",
    "CI_MODE_FEATURE_IMPROVEMENTS",
    "DEFAULT_CI_MODE_INTERVAL_HOURS",
    "DEFAULT_CI_MAX_IMPROVEMENT_TICKETS_PER_RUN",
    "SUPPORTED_TRACKER_KINDS",
    "SUPPORTED_WORKSPACE_REUSE_POLICIES",
    "DEFAULT_AGENT_KIND",
    "DEFAULT_BOARD_ROOT_NAME",
    "DEFAULT_PROMPT",
    "DEFAULT_ACTIVE_STATES",
    "DEFAULT_TERMINAL_STATES",
    "DEFAULT_AUTO_MERGE_EXCLUDE_PATHS",
    "SYMPHONY_BRANCH_PREFIX",
    "DEFAULT_MAX_REOPENS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_AGY_COMMAND",
    "DEFAULT_KIRO_COMMAND",
    "DEFAULT_OPENCODE_COMMAND",
    "DEFAULT_PRIME_AGENT_COMMAND",
    "LINEAR_API_KEY_ENV",
    "LINEAR_DEFAULT_ENDPOINT",
    "JIRA_API_TOKEN_ENV",
    "JIRA_EMAIL_ENV",
]
