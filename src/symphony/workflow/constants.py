"""SPEC §5, §6 — defaults, supported sets, env-key names, var-indirection regex.

This module is the single source of truth for every default value the
WORKFLOW.md parser applies and every supported-value set the validators
check against. Keeping it dependency-free (no symphony imports) means
test fixtures can reach in for an env name or default integer without
pulling the whole config builder.
"""

from __future__ import annotations

import re

SUPPORTED_TRACKER_KINDS = {"linear", "file", "jira"}
LINEAR_DEFAULT_ENDPOINT = "https://api.linear.app/graphql"
LINEAR_API_KEY_ENV = "LINEAR_API_KEY"
# Jira Cloud Basic Auth uses (account email, API token).
# Tokens are minted at id.atlassian.com → "Manage account" → "Security".
JIRA_API_TOKEN_ENV = "JIRA_API_TOKEN"
JIRA_EMAIL_ENV = "JIRA_EMAIL"

DEFAULT_ACTIVE_STATES = ("Todo", "In Progress", "Verify", "Document")
DEFAULT_TERMINAL_STATES = (
    "Human Review",
    "Done",
    "Archive",
    "Blocked",
    "Cancelled",
    "Canceled",
    "Closed",
    "Duplicate",
)
DEFAULT_BOARD_ROOT_NAME = "board"
DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 1
DEFAULT_MAX_TURNS = 100
DEFAULT_MAX_TOTAL_TURNS = 200
DEFAULT_MAX_STATE_TURNS = 30
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_REOPENS = 3
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_AUTO_RECOVER_BLOCKED = True
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_CODEX_TURN_TIMEOUT_MS = 3_600_000
# Codex `thread/start` sync responses have been measured at ~4.9 s under
# load, so a 5 s read budget intermittently timed out session creation.
# 20 s keeps startup hangs detectable while clearing that latency band.
DEFAULT_CODEX_READ_TIMEOUT_MS = 20_000
DEFAULT_CODEX_STALL_TIMEOUT_MS = 300_000
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "high"
DEFAULT_WORKSPACE_REUSE_POLICY = "preserve"
SUPPORTED_WORKSPACE_REUSE_POLICIES = {"preserve", "refresh"}

DEFAULT_PROMPT = "You are working on an issue from Linear."

# Continuous-improvement heartbeat (default off).
DEFAULT_CI_INTERVAL_MS = 1_800_000
DEFAULT_CI_MIN_INTERVAL_MS = 60_000
DEFAULT_CI_MAX_TURNS = 48
DEFAULT_CI_TICKET_PREFIX = "CI"
DEFAULT_CI_MAX_TICKETS_PER_RUN = 5

# Improvement modes (experimental, each opt-in through
# `continuous_improvement.modes`). `readiness` is the original
# product-readiness inspection and stays the implicit default so an existing
# `enabled: true` block keeps its old behaviour.
CI_MODE_READINESS = "readiness"
CI_MODE_BLOCKED_FIXES = "blocked_fixes"
CI_MODE_SECURITY = "security"
CI_MODE_MARKET_RESEARCH = "market_research"
CI_MODE_FEATURE_IMPROVEMENTS = "feature_improvements"
SUPPORTED_CI_MODES = (
    CI_MODE_READINESS,
    CI_MODE_BLOCKED_FIXES,
    CI_MODE_SECURITY,
    CI_MODE_MARKET_RESEARCH,
    CI_MODE_FEATURE_IMPROVEMENTS,
)
# Modes that need a real agent turn (the orchestrator supplies the runner).
CI_AGENT_MODES = (CI_MODE_MARKET_RESEARCH, CI_MODE_FEATURE_IMPROVEMENTS)
# Per-mode cadence floor, in hours. 0 = every heartbeat.
DEFAULT_CI_MODE_INTERVAL_HOURS: dict[str, float] = {
    CI_MODE_READINESS: 0.0,
    CI_MODE_BLOCKED_FIXES: 0.0,
    CI_MODE_SECURITY: 24.0,
    CI_MODE_MARKET_RESEARCH: 168.0,
    CI_MODE_FEATURE_IMPROVEMENTS: 72.0,
}
# Agent/triage modes file proposals, not check failures — capped separately
# from `max_tickets_per_run` so a chatty research turn cannot flood the board.
DEFAULT_CI_MAX_IMPROVEMENT_TICKETS_PER_RUN = 3

SUPPORTED_AGENT_KINDS = {"agy", "codex", "claude", "gemini", "kiro", "opencode", "pi", "prime-agent"}
DEFAULT_AGENT_KIND = "codex"
DEFAULT_CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --include-partial-messages --verbose"
)
# `gemini -p` (no argument) prints help and exits in Gemini CLI 0.39+ — the
# `-p`/`--prompt` flag now requires a string. We pass an empty string so
# stdin alone is the prompt (Gemini documents stdin as "Appended to input on
# stdin (if any).").
DEFAULT_GEMINI_COMMAND = 'gemini -p ""'
# Antigravity CLI print mode requires the prompt as an argument and ignores
# piped stdin when given a literal `-`, so bridge stdin through the shell.
DEFAULT_AGY_COMMAND = 'agy --print "$(cat)"'
# Kiro CLI headless mode. Kiro's noninteractive chat command does not consume
# piped stdin as the first message, so the shell bridges Symphony's stdin into
# the required positional input argument.
DEFAULT_KIRO_COMMAND = (
    'kiro-cli chat --no-interactive --trust-all-tools "$(cat)"'
)
# Pi (https://pi.dev) print mode: `-p ""` lets stdin carry the full prompt;
# `--mode json` switches stdout to JSONL events so we can parse session id,
# turn boundaries, and per-message token usage.
DEFAULT_PI_COMMAND = 'pi --mode json -p ""'
# Prime Agent CLI (https://github.com/cskwork/prime-agent) — same JSON
# protocol as Pi; uses `--resume <id>` instead of `--session <id>`.
DEFAULT_PRIME_AGENT_COMMAND = 'prime-agent -p --mode json'
# OpenCode documents `opencode run [message..]` for scripting, with
# `--format json` exposing raw JSON events and `--auto` allowing
# non-interactive tool permission flow under the user's configured policy.
DEFAULT_OPENCODE_COMMAND = "opencode run --format json --auto"
DEFAULT_BACKEND_TURN_TIMEOUT_MS = 3_600_000
# Matches DEFAULT_CODEX_READ_TIMEOUT_MS: 5 s sat inside the observed
# startup-response latency band and caused intermittent timeouts.
DEFAULT_BACKEND_READ_TIMEOUT_MS = 20_000
DEFAULT_BACKEND_STALL_TIMEOUT_MS = 300_000

DEFAULT_AUTO_MERGE_EXCLUDE_PATHS: tuple[str, ...] = ()

# Feature branches are named `symphony/<identifier>`. Mirrored in
# scripts/symphony-setup-worktree.sh, which cannot import this module.
SYMPHONY_BRANCH_PREFIX = "symphony/"

_AFTER_DONE_FAILURE_POLICIES = ("warn", "block")

_VAR_PATTERN = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
