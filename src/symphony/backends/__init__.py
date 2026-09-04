"""Agent backend abstraction.

Symphony was originally hardwired to the Codex app-server JSON-RPC protocol.
This package introduces an `AgentBackend` Protocol so the orchestrator can
drive any coding-agent CLI (Codex, Claude Code, Gemini, AGY/Antigravity, Kiro,
OpenCode, Pi, Prime Agent) behind one interface.

Each backend owns its own subprocess lifecycle. The Codex backend keeps the
single long-running app-server connection that speaks JSON-RPC over stdio.
The Claude, Gemini, AGY, Kiro, OpenCode, Pi, and Prime Agent backends spawn one
subprocess per turn — Claude uses `claude -p --output-format stream-json`, Gemini
uses `gemini -p` one-shot, AGY uses `agy --print "$(cat)"`, Kiro uses
`kiro-cli chat --no-interactive`, OpenCode uses `opencode run --format json`, Pi
uses `pi --mode json -p ""`, and Prime Agent uses `prime-agent -p --mode json`.

Normalized event vocabulary is shared across backends (see `events.py` style
constants below). Per-turn backends emit `turn_started` immediately after
publishing each live child so the orchestrator can replace the process-group
identifier before any CLI output. The orchestrator only consumes these
normalized event names plus an `AgentEvent`-shaped dict, so it never sees
backend-specific protocol details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, cast, runtime_checkable

from ..errors import ConfigValidationError
from ..workflow import ServiceConfig


# Normalized event vocabulary — every backend emits these strings only.
EVENT_SESSION_STARTED = "session_started"
EVENT_STARTUP_FAILED = "startup_failed"
EVENT_TURN_STARTED = "turn_started"
EVENT_TURN_COMPLETED = "turn_completed"
EVENT_TURN_FAILED = "turn_failed"
EVENT_TURN_CANCELLED = "turn_cancelled"
EVENT_TURN_ENDED_WITH_ERROR = "turn_ended_with_error"
EVENT_TURN_INPUT_REQUIRED = "turn_input_required"
EVENT_APPROVAL_AUTO_APPROVED = "approval_auto_approved"
EVENT_APPROVAL_DENIED = "approval_denied"
EVENT_UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
EVENT_NOTIFICATION = "notification"
EVENT_OTHER_MESSAGE = "other_message"
EVENT_MALFORMED = "malformed"
# Pi-flavoured signals — backend may translate native events to these so the
# orchestrator can log/observe them with cross-backend semantics.
EVENT_COMPACTION = "compaction"           # context compaction started/ended
EVENT_AGENT_RETRY = "agent_retry"         # backend-internal auto-retry

# R7 — stream robustness knobs shared by the line-streaming backends.
# A stream that is nothing but garbage should fail with a parse error, not
# degrade into the generic "no terminal event" message; sparse bad lines
# (any valid line resets the streak) stay tolerated.
MALFORMED_LINE_LIMIT = 10
# Post-stream reap bound: a child that closes stdout but lingers used to
# hang the turn forever on an untimed safe_proc_wait.
POST_STREAM_REAP_TIMEOUT_S = 10.0

# Session identifiers cross a persistence/process boundary during crash
# recovery. Keep the accepted surface deliberately small and bounded before a
# concrete backend forwards an exact id to a CLI or app-server.
MAX_SESSION_ID_LENGTH = 512


def _is_valid_session_id(session_id: object) -> bool:
    """Return whether *session_id* is safe to forward to an agent backend."""
    return (
        isinstance(session_id, str)
        and bool(session_id.strip())
        and len(session_id) <= MAX_SESSION_ID_LENGTH
        and all(char.isprintable() for char in session_id)
    )


def redact_session_id(value: Any, session_id: str | None) -> Any:
    """Remove an exact private session handle from nested backend evidence."""
    if not session_id:
        return value
    if isinstance(value, str):
        return value.replace(session_id, "[REDACTED_SESSION]")
    if isinstance(value, dict):
        return {key: redact_session_id(item, session_id) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_session_id(item, session_id) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_session_id(item, session_id) for item in value)
    return value


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TurnResult:
    status: str
    turn_id: str | None
    last_message: str = ""
    error: str | None = None


@dataclass
class ToolDescriptor:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class BackendInit:
    """Constructor inputs every backend needs.

    Keeping construction parameter list as a dataclass keeps the factory and
    tests honest — adding a new field forces every backend to acknowledge it.
    """

    cfg: ServiceConfig
    cwd: Path
    workspace_root: Path
    on_event: EventCallback
    on_process_started: Callable[[int], None] | None = None
    client_tools: list[ToolDescriptor] = field(default_factory=list)
    # Per-dispatch environment overlaid on the inherited process environment
    # when the backend spawns its subprocess (``SYMPHONY_TOKEN_EMA``,
    # ``SYMPHONY_TOKEN_BUDGET``, ``SYMPHONY_REWIND_SCOPE``). Carrying it here
    # instead of mutating ``os.environ`` keeps concurrent dispatches from
    # clobbering each other's values (GitHub issue #29).
    env: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class AgentBackend(Protocol):
    """Lifecycle contract for a coding-agent CLI driver.

    Order of calls from the orchestrator:
        await b.start()
        await b.initialize()
        await b.start_session(initial_prompt=..., issue_title=...)
        for each turn:
            await b.run_turn(prompt=..., is_continuation=...)
        await b.stop()

    Backends MUST emit normalized events through `on_event` for at least:
    - `session_started` once a session id is known,
    - `turn_started` with `agent_pid` once each per-turn child is live,
    - `turn_completed` / `turn_failed` / `turn_cancelled` per turn outcome.

    Token + rate-limit telemetry is reported by the latest_* properties so the
    orchestrator can roll up totals without reaching into protocol payloads.
    """

    async def start(self) -> None: ...

    async def initialize(self) -> dict[str, Any]: ...

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str: ...

    async def resume_session(self, session_id: str) -> bool: ...

    async def run_turn(
        self, *, prompt: str, is_continuation: bool
    ) -> TurnResult: ...

    async def stop(self) -> None: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def pid(self) -> int | None: ...

    @property
    def latest_usage(self) -> dict[str, int]: ...

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None: ...

    def is_progress_event(self, event: dict[str, Any]) -> bool: ...


class BaseAgentBackend:
    """Shared default behaviour for concrete backends.

    Concrete backends inherit this so any future cross-cutting default
    (currently just `is_progress_event`) lives in one place rather than
    being copy-pasted into each driver.

    Backends still match the `AgentBackend` Protocol structurally — the
    base class is purely additive and does not constrain construction.
    """

    async def resume_session(self, session_id: str) -> bool:
        """Try to continue an exact prior session.

        Unsupported backends fail closed. Concrete backends must validate ids
        before forwarding them and return ``False`` when exact continuation
        cannot be established so the caller can safely start a fresh session.
        """
        del session_id
        return False

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        """Return True when an event should reset the stall-progress timer.

        Default is conservative: every event counts as progress so a new
        backend doesn't accidentally trigger spurious stall cancellations.
        Backends that produce keepalive / tool-echo events between real
        model output (e.g. claude stream-json `user` frames carrying
        `tool_result` echoes) override this to filter them out — see
        `ClaudeCodeBackend.is_progress_event` for the canonical example.
        """
        del event
        return True


def build_backend(init: BackendInit) -> AgentBackend:
    """Factory: pick a concrete backend by `agent.kind`.

    Each concrete backend inherits `is_progress_event` from
    `BaseAgentBackend` and structurally satisfies `AgentBackend`. Pyright
    does not always trace Protocol membership through a non-Protocol base
    class for methods that are only inherited (no override), so we `cast`
    here to declare the structural compatibility explicitly. Runtime
    duck-typing through `isinstance(..., AgentBackend)` still works
    thanks to `@runtime_checkable`.
    """
    kind = init.cfg.agent.kind
    if kind == "codex":
        from .codex import CodexAppServerBackend

        return cast(AgentBackend, CodexAppServerBackend(init))
    if kind == "claude":
        from .claude_code import ClaudeCodeBackend

        return cast(AgentBackend, ClaudeCodeBackend(init))
    if kind == "gemini":
        from .gemini import GeminiBackend

        return cast(AgentBackend, GeminiBackend(init))
    if kind == "agy":
        from .agy import AgyBackend

        return cast(AgentBackend, AgyBackend(init))
    if kind == "kiro":
        from .kiro import KiroBackend

        return cast(AgentBackend, KiroBackend(init))
    if kind == "opencode":
        from .opencode import OpenCodeBackend

        return cast(AgentBackend, OpenCodeBackend(init))
    if kind == "pi":
        from .pi import PiBackend

        return cast(AgentBackend, PiBackend(init))
    if kind == "prime-agent":
        from .prime_agent import PrimeAgentBackend

        return cast(AgentBackend, PrimeAgentBackend(init))
    raise ConfigValidationError(
        "unknown agent.kind "
        f"{kind!r}; expected agy, codex, claude, gemini, kiro, opencode, "
        f"pi, or prime-agent"
    )
