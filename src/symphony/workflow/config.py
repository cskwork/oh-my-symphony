"""SPEC §4.1.3, §6.4 — frozen typed config view exposed to the orchestrator.

Every field a long-running workflow can carry — tracker, hooks, the four
backend kinds, TUI/server/progress/system/wiki extras, prompt template
overrides — lives here as a `@dataclass(frozen=True)` value type. The
builder (`build_service_config`) is the only writer; the orchestrator and
TUI only read.

`ServiceConfig.prompt_template_for_state` and `backend_timeouts` are the
small handful of methods kept on the value types because they project
state-local views (per-state prompts, active-backend timeouts) that
otherwise would force every caller to recompute the same `if/elif` ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..notifications import NotificationsConfig
from .coercion import _normalize_state_key
from .constants import (
    DEFAULT_AUTO_MERGE_EXCLUDE_PATHS,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOTAL_TURNS,
    DEFAULT_WORKSPACE_REUSE_POLICY,
)


@dataclass(frozen=True)
class TrackerConfig:
    kind: str
    endpoint: str
    api_key: str
    project_slug: str
    active_states: tuple[str, ...]
    terminal_states: tuple[str, ...]
    # tracker.kind=file: absolute path to the board directory.
    board_root: Path | None = None
    # Optional one-line description rendered as a legend under each column
    # title in the TUI. Keys are state names (case-insensitive match against
    # active_states / terminal_states); values are short human-readable
    # explanations of what work happens in that lane.
    state_descriptions: dict[str, str] = field(default_factory=dict)
    # Auto-archive sweep — every poll tick, terminal-state issues whose
    # `updated_at` is older than `archive_after_days` get moved to the
    # `archive_state` lane. Set `archive_after_days` to 0 to disable
    # sweep entirely (the manual TUI hotkey still works). The `archive_state`
    # name must also appear in `terminal_states` so the lane renders.
    archive_state: str = "Archive"
    archive_after_days: int = 30
    # tracker.kind=jira: Atlassian account email paired with `api_key` (the
    # API token) for Basic Auth against Jira Cloud. Linear/file adapters
    # ignore this field. Defaults to empty so existing callers stay
    # source-compatible.
    email: str = ""


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None
    before_run: str | None
    after_run: str | None
    before_remove: str | None
    timeout_ms: int
    # Fires once per ticket immediately after `commit_workspace_on_done`
    # succeeds AND the ticket reached `Done`. Receives the standard hook
    # env plus `SYMPHONY_ISSUE_ID` and `SYMPHONY_ISSUE_TITLE`. Lenient —
    # failures only log a warning and never block worker cleanup. Default
    # None preserves legacy behaviour and keeps existing positional
    # `HooksConfig(...)` callers source-compatible.
    after_done: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    kind: str
    max_concurrent_agents: int
    max_turns: int
    max_retry_backoff_ms: int
    max_concurrent_agents_by_state: dict[str, int]
    max_total_turns: int = DEFAULT_MAX_TOTAL_TURNS
    # Soft cap for Review/QA rewinds back into In Progress. 0 disables.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # Cap on auto-retries scheduled after a worker exits with a non-normal
    # outcome (timeout, crash, transient backend error). On exhaustion the
    # orchestrator stops scheduling further retries, appends an
    # `## Escalation` note to the ticket explaining what happened, and
    # moves the ticket to the configured terminal state (`Blocked` by
    # default) so it surfaces on the board instead of looping silently.
    # 0 disables the cap (legacy behaviour: retry forever with backoff).
    max_retries: int = DEFAULT_MAX_RETRIES
    # File-board optimization: actionable Todo tickets can be routed to Explore
    # by the orchestrator without spending a model turn on one-line triage.
    auto_triage_actionable_todo: bool = True
    # When a ticket reaches the Done state cleanly, snapshot the workspace
    # into a single git commit (`git init` if no enclosing repo found).
    # Default ON so a fresh `pip install oh-my-symphony` plus a
    # WORKFLOW.md is enough to get a per-ticket commit trail without
    # wiring an after_run hook. Set to false in WORKFLOW.md when the
    # workspace is e.g. an existing repo with strict commit-style rules.
    auto_commit_on_done: bool = True
    # After auto-commit on Done, optionally fold the symphony/<ID> branch
    # back into the host repo's main development branch with an explicit
    # `git merge --no-ff` commit. Paths in `auto_merge_exclude_paths` are
    # guardrails: if any of them changed on the branch, the merge is
    # blocked because those roots are workspace plumbing, not deliverables.
    # Keep docs branch-local so reports/wiki updates ride with the merge.
    # Safe-by-default: a dirty host working tree that overlaps branch
    # changes or any git error skips the merge and logs an event — no
    # exception propagates.
    auto_merge_on_done: bool = True
    # Target branch in the host repo. Empty string ("") = use whatever
    # branch is currently checked out in the host repo at fire time.
    auto_merge_target_branch: str = ""
    # Branch/ref used as the start point when creating new per-ticket feature
    # worktrees. Empty string ("") = use the host repo's current branch.
    feature_base_branch: str = ""
    # Workspace-only roots that must not differ on the ticket branch.
    # File-board workflows usually set this to `["kanban"]`; add `prompt`
    # only if your hook symlinks it from the host. Do not list `docs`
    # unless you intentionally made docs host-owned and accept that docs
    # will not be branch deliverables.
    auto_merge_exclude_paths: tuple[str, ...] = DEFAULT_AUTO_MERGE_EXCLUDE_PATHS
    # Legacy escape hatch for workflows that intentionally keep a report
    # tree host-owned. Prefer branch-local docs instead. Captured files are
    # added to the same `--no-ff` merge commit.
    auto_merge_capture_untracked: tuple[str, ...] = ()
    # What to do when the `after_done` hook fails. "warn" (default,
    # legacy) just logs `hook_after_done_failed` and the orchestrator
    # removes the workspace as usual — a failed dev/prod-apply script
    # then looks like a clean Done. "block" preserves the workspace,
    # marks the ticket with `last_error`, and skips workspace removal so
    # an operator can investigate before the worktree is reaped. Pair
    # with a production-critical `after_done` (deploy / apply-to-host)
    # to avoid silent partial completions.
    after_done_failure_policy: str = "warn"
    # Hard cap on state-local `total_tokens` (input + output across turns
    # while the ticket remains in one state). The counter resets on each
    # state transition, while lifetime totals stay visible in the API. 0 =
    # disabled (legacy). When set, `_on_codex_event` cancels the worker
    # the moment the current state's total crosses the cap and marks
    # `last_error="token budget exceeded"` so an operator sees the brake
    # reason without log-diving. Pair with a generous `max_turns` to catch
    # runaway-reasoning loops that the progress-timestamp stall predicate
    # can't see because turns ARE completing.
    max_total_tokens: int = 0
    # Optional per-state override for `max_total_tokens`. Keys are state
    # names lowercased by the parser, e.g. "review" or "in progress".
    max_total_tokens_by_state: dict[str, int] = field(default_factory=dict)
    # Target tracker state to transition the ticket to when
    # `max_total_turns` is exhausted. Empty string (default, legacy) =
    # no transition; the in-memory `_turn_budget_exhausted` guard alone
    # suppresses re-dispatch within this process — a service restart
    # then clears the guard and the same ticket can run again. Set this
    # to a non-active state name (e.g. "Blocked" or your tracker's
    # equivalent) to persist the exhaustion via the tracker, so the
    # decision survives restart and reaches operators reviewing the
    # board. Must match a state your tracker.kind backend can write to.
    budget_exhausted_state: str = ""


@dataclass(frozen=True)
class CodexConfig:
    command: str
    approval_policy: Any
    thread_sandbox: Any
    turn_sandbox_policy: Any
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT


@dataclass(frozen=True)
class ClaudeConfig:
    """`agent.kind: claude` — driving Claude Code CLI in print/stream mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, turns 2+ within one worker attempt add `--resume <session_id>`
    # so Claude rejoins the prior session instead of starting fresh. Cross-
    # attempt resume (after a worker error / retry) is intentionally NOT
    # supported — each retry attempt builds a new backend instance, so the
    # captured session id is discarded with the prior worker.
    resume_across_turns: bool


@dataclass(frozen=True)
class GeminiConfig:
    """`agent.kind: gemini` — driving Gemini CLI in JSON/session mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, turns 2+ within one worker attempt add `--resume <id>` so
    # Gemini rejoins the session UUID minted at start_session. Cross-attempt
    # and cross-phase resume is intentionally not supported because those
    # paths build a new backend instance.
    resume_across_turns: bool = True


@dataclass(frozen=True)
class PiConfig:
    """`agent.kind: pi` — driving the Pi coding-agent CLI in print/json mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, turns 2+ within one worker attempt add `--session <id>` so Pi
    # rejoins the prior session. Cross-attempt resume is intentionally not
    # supported — each retry attempt builds a new backend instance.
    resume_across_turns: bool


@dataclass(frozen=True)
class ServerConfig:
    """§13.7 optional HTTP extension."""

    port: int | None


@dataclass(frozen=True)
class TuiConfig:
    """Display-time TUI tweaks. Affects rendering only; orchestrator ignores."""

    # ISO-639-1 language code used to look up localized chrome strings
    # (column placeholder, header / footer field labels, card meta verbs).
    # Tracker state names, ticket titles, and `state_descriptions` come from
    # user data and are never translated. Defaults to "en".
    language: str = "en"

    # How many Kanban lanes show simultaneously in the board. The remaining
    # lanes are paged off-screen — `t` cycles to the next window of lanes,
    # `shift+t` to the previous, `+`/`-` grow/shrink the window at runtime.
    # Default 5 keeps each card column wide enough to read on a 120-col
    # terminal even with the default detail pane visible. The TUI clamps
    # values <1 up to 1 so a malformed config doesn't blank the board.
    visible_lanes: int = 5


@dataclass(frozen=True)
class ProgressConfig:
    """Optional WORKFLOW-PROGRESS.md mirror written by the orchestrator.

    `path` defaults to `WORKFLOW-PROGRESS.md` next to WORKFLOW.md when the
    user enables progress without specifying a path. `enabled=True` is the
    out-of-the-box default; the CLI's `--no-progress-md` flag flips it off
    without editing the workflow file.
    """

    enabled: bool = True
    path: Path | None = None
    max_transitions: int = 20


@dataclass(frozen=True)
class SystemConfig:
    """Host-OS integration toggles.

    `keep_awake` prevents macOS from sleeping or locking the display while
    the orchestrator is running. The CLI launches `caffeinate -d -i -w <pid>`
    as a child; non-macOS hosts treat the flag as a no-op. CLI flag
    `--no-keep-awake` overrides this for one run.
    """

    keep_awake: bool = True


@dataclass(frozen=True)
class WikiConfig:
    """Wiki integrity sweep config (C5).

    `sweep_every_n` controls how often the orchestrator runs `symphony
    wiki-sweep` automatically after a `Done` transition. 0 disables the
    auto-sweep entirely; the manual CLI subcommand still works. `root`
    is the wiki directory the sweep walks (defaults to `docs/llm-wiki`
    relative to the workflow file).
    """

    sweep_every_n: int = 10
    root: Path | None = None


@dataclass(frozen=True)
class PromptConfig:
    """External prompt files configured from WORKFLOW.md.

    `base_template` is shared across all states. `stage_templates` is keyed
    by normalized tracker state and contains only the current-stage rule body.
    """

    base_template: str = ""
    base_path: Path | None = None
    stage_templates: dict[str, str] = field(default_factory=dict)
    stage_paths: dict[str, Path] = field(default_factory=dict)

    def has_stage_prompts(self) -> bool:
        return bool(self.stage_templates)


@dataclass(frozen=True)
class ServiceConfig:
    workflow_path: Path
    poll_interval_ms: int
    workspace_root: Path
    tracker: TrackerConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    claude: ClaudeConfig
    gemini: GeminiConfig
    pi: PiConfig
    server: ServerConfig
    tui: TuiConfig = field(default_factory=TuiConfig)
    progress: ProgressConfig = field(default_factory=ProgressConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    prompts: PromptConfig = field(default_factory=PromptConfig)
    wiki: WikiConfig = field(default_factory=WikiConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""
    workspace_reuse_policy: str = DEFAULT_WORKSPACE_REUSE_POLICY

    def prompt_template_for_state(self, state: str) -> str:
        """Return the runtime prompt template for one tracker state."""
        key = _normalize_state_key(state)
        stage_template = self.prompts.stage_templates.get(key)
        if stage_template is None:
            return self.prompt_template
        parts = [self.prompts.base_template, stage_template]
        return "\n\n".join(part for part in parts if part)

    def backend_timeouts(self) -> tuple[int, int, int]:
        """Return `(turn_ms, read_ms, stall_ms)` for the active backend."""
        kind = self.agent.kind
        if kind == "codex":
            return (
                self.codex.turn_timeout_ms,
                self.codex.read_timeout_ms,
                self.codex.stall_timeout_ms,
            )
        if kind == "claude":
            return (
                self.claude.turn_timeout_ms,
                self.claude.read_timeout_ms,
                self.claude.stall_timeout_ms,
            )
        if kind == "pi":
            return (
                self.pi.turn_timeout_ms,
                self.pi.read_timeout_ms,
                self.pi.stall_timeout_ms,
            )
        return (
            self.gemini.turn_timeout_ms,
            self.gemini.read_timeout_ms,
            self.gemini.stall_timeout_ms,
        )
