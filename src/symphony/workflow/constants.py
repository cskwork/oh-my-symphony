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

DEFAULT_ACTIVE_STATES = ("Todo", "Explore", "Plan", "In Progress", "Review", "QA", "Learn")
DEFAULT_TERMINAL_STATES = (
    "Closed",
    "Cancelled",
    "Canceled",
    "Duplicate",
    "Human Review",
    "Done",
    "Archive",
)
DEFAULT_BOARD_ROOT_NAME = "board"
DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 1
DEFAULT_MAX_TURNS = 100
DEFAULT_MAX_TOTAL_TURNS = 200
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_CODEX_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_CODEX_READ_TIMEOUT_MS = 5_000
DEFAULT_CODEX_STALL_TIMEOUT_MS = 300_000
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "high"
DEFAULT_WORKSPACE_REUSE_POLICY = "preserve"
SUPPORTED_WORKSPACE_REUSE_POLICIES = {"preserve", "refresh"}

DEFAULT_PROMPT = "You are working on an issue from Linear."

SUPPORTED_AGENT_KINDS = {"codex", "claude", "gemini", "pi"}
DEFAULT_AGENT_KIND = "codex"
DEFAULT_CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --include-partial-messages --verbose"
)
# `gemini -p` (no argument) prints help and exits in Gemini CLI 0.39+ — the
# `-p`/`--prompt` flag now requires a string. We pass an empty string so
# stdin alone is the prompt (Gemini documents stdin as "Appended to input on
# stdin (if any).").
DEFAULT_GEMINI_COMMAND = 'gemini -p ""'
# Pi (https://pi.dev) print mode: `-p ""` lets stdin carry the full prompt;
# `--mode json` switches stdout to JSONL events so we can parse session id,
# turn boundaries, and per-message token usage.
DEFAULT_PI_COMMAND = 'pi --mode json -p ""'
DEFAULT_BACKEND_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_BACKEND_READ_TIMEOUT_MS = 5_000
DEFAULT_BACKEND_STALL_TIMEOUT_MS = 300_000

DEFAULT_AUTO_MERGE_EXCLUDE_PATHS: tuple[str, ...] = ()

_AFTER_DONE_FAILURE_POLICIES = ("warn", "block")

_VAR_PATTERN = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
