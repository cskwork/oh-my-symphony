"""`symphony doctor` — preflight checks for a WORKFLOW.md.

Verifies that the most common first-run failures are absent before the user
launches `symphony tui` or the headless service:

- Port for the JSON API is bindable (catches the EADDRINUSE that crashed the
  start path with a raw OSError).
- The agent CLI matching `agent.kind` is on `$PATH`.
- `hooks.after_create` is not a stale placeholder `my-org/my-repo` URL
  (relevant when the operator overrode the worktree default with a
  clone-mode hook but forgot to change the remote).
- `workspace.root` exists and is writable.
- File-tracker `tracker.board_root` exists; Linear-tracker `api_key` resolves.

Exit codes:
    0  — all checks passed (warnings allowed)
    1  — at least one check failed
    2  — could not load WORKFLOW.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .._shell import _is_wsl_launcher, resolve_bash
from ..backends.codex import _sandbox_uses_workspace_write
from ..errors import SymphonyError
from ..issue import normalize_state
from ..runtime_safety import (
    PROTECTED_REPOSITORY_MESSAGE,
    workflow_uses_protected_source_repo,
)
from ..orchestrator.release_contracts import inspect_release_contract
from ..orchestrator.release_cycle import (
    has_release_finalizer_lane,
    has_release_success_terminal,
)
from ..trackers.file import FileBoardTracker
from ..utils.git_sandbox import resolve_git_common_dir, writable_git_roots
from ..service import ProcessRunningPredicate, port_owner_hint
from ..workflow import (
    ServiceConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)
from ..workflow.preflight import stage_turn_budget_error
from ..workflow.presets import guess_lane_preset


Status = Literal["pass", "warn", "fail"]

# Module-level runtime bool. Pyright narrows literal `sys.platform == "win32"`
# at evaluation time (so the Win branch is "unreachable" on macOS/Linux), but
# does NOT narrow a separately-bound bool. Use this everywhere we need the
# platform gate so cross-platform branches stay analyzable on every host.
_IS_WIN32: bool = sys.platform == "win32"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str


def _bind_port(
    host: str,
    port: int,
    *,
    workflow_path: Path | None = None,
    is_running: ProcessRunningPredicate | None = None,
) -> CheckResult:
    name = f"server.port={port}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Match asyncio's TCP listener: POSIX servers may replace TIME_WAIT
    # sockets, while Windows keeps its exclusive default semantics.
    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        0 if _IS_WIN32 else 1,
    )
    try:
        sock.bind((host, port))
    except OSError as exc:
        hint = (
            port_owner_hint(workflow_path, port, is_running=is_running)
            if workflow_path is not None
            else None
        )
        suffix = hint or (
            "run `symphony service status <workflow>` or "
            f"`lsof -ti :{port}` to identify the owner"
        )
        return CheckResult(
            name, "fail", f"cannot bind {host}:{port} — {exc}; {suffix}"
        )
    finally:
        sock.close()
    return CheckResult(name, "pass", f"{host}:{port} is free")


def check_port(
    cfg: ServiceConfig,
    host: str = "127.0.0.1",
    *,
    is_running: ProcessRunningPredicate | None = None,
) -> CheckResult:
    if cfg.server.port is None:
        return CheckResult("server.port", "pass", "no HTTP API configured (server.port unset)")
    return _bind_port(
        host,
        cfg.server.port,
        workflow_path=cfg.workflow_path,
        is_running=is_running,
    )


def check_api_token_env(cfg: ServiceConfig) -> CheckResult:
    """Advisory: `SYMPHONY_API_TOKEN` with internal whitespace never matches.

    The bearer parse splits the Authorization header on whitespace and
    requires exactly two words, so a token that itself contains a space or
    tab can never authenticate — every request 401s and the web board sits
    behind an unfixable prompt. Leading/trailing whitespace is fine (the
    value is stripped); internal whitespace is the footgun. Kept as a
    warning, not a failure: the env var still gates correctly for clients
    that never authenticate, and the operator may be mid-edit.
    """
    name = "server.api_token"
    del cfg  # env-only check; the signature keeps the common shape
    raw = os.environ.get("SYMPHONY_API_TOKEN")
    if raw is None:
        return CheckResult(name, "pass", "unset (no credential gate)")
    token = raw.strip()
    if not token:
        return CheckResult(name, "pass", "blank (behaves as unset)")
    if any(ch.isspace() for ch in token):
        return CheckResult(
            name,
            "warn",
            "SYMPHONY_API_TOKEN contains internal whitespace — clients send"
            " it as one bearer word, so this value can never match and every"
            " /api/ request will 401; remove the inner spaces (or fix a"
            " mis-quoted export)",
        )
    return CheckResult(name, "pass", "set; bearer token required on /api/")


def check_agent_cli(cfg: ServiceConfig) -> CheckResult:
    kind = cfg.agent.kind
    if kind == "codex":
        command = cfg.codex.command
    elif kind == "claude":
        command = cfg.claude.command
    elif kind == "gemini":
        command = cfg.gemini.command
    elif kind == "agy":
        command = cfg.agy.command
    elif kind == "kiro":
        command = cfg.kiro.command
    elif kind == "opencode":
        command = cfg.opencode.command
    elif kind == "pi":
        command = cfg.pi.command
    elif kind == "prime-agent":
        command = cfg.prime_agent.command
    else:
        return CheckResult(f"agent.kind={kind}", "fail", f"unsupported agent kind {kind!r}")

    name = f"agent.kind={kind}"
    try:
        if _IS_WIN32:
            # POSIX-mode shlex eats backslashes: `D:\tools\python.exe`
            # would reach shutil.which as `D:toolspython.exe` and the
            # preflight bogus-fails with "not on $PATH". Whitespace mode
            # keeps backslashes (and the quotes shlex leaves attached to
            # a quoted token), so strip those surrounding quotes from the
            # binary before resolving it.
            argv = shlex.split(command, posix=False)
            if argv:
                argv[0] = argv[0].strip("\"'")
        else:
            argv = shlex.split(command)
    except ValueError as exc:
        return CheckResult(name, "fail", f"command not parseable: {exc}")
    if not argv:
        return CheckResult(name, "fail", f"{kind}.command is empty")

    binary = argv[0]
    # `python -m symphony.mock_codex` style — find the interpreter, not the module.
    located = shutil.which(binary)
    if located is None and binary == "python":
        located = sys.executable
    if located is None:
        return CheckResult(name, "fail", f"{binary!r} not on $PATH (configured: {command!r})")
    return CheckResult(name, "pass", f"{binary} → {located}")


_PLACEHOLDER_TOKENS = ("my-org/my-repo", "my-org:my-repo")
_SETUP_FAILURE_STRINGS = (
    "PrismaConfigEnvError",
    "Cannot resolve environment variable",
    "Traceback",
    "ModuleNotFoundError",
)
_MASKED_AFTER_CREATE_TAIL = re.compile(
    r"(?:^|[|;&]\s*)tail\s+(?:-\d+\b|-n(?:\s+\d+|\b))"
)


def _after_create_source_lines(cfg: ServiceConfig, hook: str) -> dict[int, int]:
    """Best-effort map from hook-local line numbers to WORKFLOW.md lines."""
    try:
        lines = cfg.workflow_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    hook_lines = hook.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not re.match(r"after_create\s*:", stripped):
            continue
        after_colon = stripped.split(":", 1)[1].strip()
        if after_colon.startswith(("|", ">")):
            return {
                local_line: index + 1 + local_line
                for local_line in range(1, len(hook_lines) + 1)
            }
        return {1: index + 1}
    return {}


def _format_hook_source_line(
    cfg: ServiceConfig, local_line_no: int, source_lines: dict[int, int]
) -> str:
    source_line = source_lines.get(local_line_no)
    if source_line is None:
        return f"line {local_line_no}"
    return f"{cfg.workflow_path.name}:{source_line}"


def _warning_after_create_lines(cfg: ServiceConfig, hook: str) -> list[str]:
    source_lines = _after_create_source_lines(cfg, hook)
    masked: list[str] = []
    for line_no, raw_line in enumerate(hook.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        has_masking = "|| true" in line or _MASKED_AFTER_CREATE_TAIL.search(line)
        has_failure_text = any(token in line for token in _SETUP_FAILURE_STRINGS)
        if not has_masking and not has_failure_text:
            continue
        masked.append(
            f"{_format_hook_source_line(cfg, line_no, source_lines)}: {line}"
        )
    return masked


def check_pi_auth(cfg: ServiceConfig) -> CheckResult:
    """When agent.kind=pi, warn if `~/.pi/agent/auth.json` is missing.

    Without it, the first dispatched turn spawns `pi --mode json` which exits
    immediately with an unauth error — cryptic when surfaced as a generic
    `turn_failed`. Catching it here is non-fatal (warn) because pi can also
    pick up auth from PI_API_KEY-style env var setups; we only flag the
    common cached-OAuth case.
    """
    name = "agent.kind=pi.auth"
    if cfg.agent.kind != "pi":
        return CheckResult(name, "pass", "not pi (skipped)")
    auth = Path.home() / ".pi" / "agent" / "auth.json"
    if auth.exists():
        return CheckResult(name, "pass", f"{auth} present")
    return CheckResult(
        name,
        "warn",
        f"{auth} not found — run `pi` and `/login` once, or ensure your"
        " provider env var is set, otherwise every dispatch will fail at the"
        " first turn.",
    )


def check_prime_agent_auth(cfg: ServiceConfig) -> CheckResult:
    """Advisory check for Prime Agent's cached provider credentials.

    Prime Agent can also resolve credentials from provider-specific environment
    variables, so a missing auth file is only a warning.  Do not parse the
    file here: this check must not expose or reject provider-specific token
    formats before the CLI gets a chance to resolve them.
    """
    name = "agent.kind=prime-agent.auth"
    if cfg.agent.kind != "prime-agent":
        return CheckResult(name, "pass", "not prime-agent (skipped)")

    configured_dir = os.environ.get("PRIME_AGENT_CODING_AGENT_DIR")
    auth_dir = (
        Path(configured_dir).expanduser()
        if configured_dir
        else Path.home() / ".prime" / "agent"
    )
    auth = auth_dir / "auth.json"
    if auth.exists():
        return CheckResult(name, "pass", f"{auth} present")
    return CheckResult(
        name,
        "warn",
        f"{auth} not found — run `prime-agent` and `/login` once, or ensure a"
        " provider API key is available in the environment; this check is"
        " advisory because Prime Agent supports multiple auth mechanisms.",
    )


# Keep the shorter name available for callers that refer to the backend as
# ``prime`` rather than by its configured ``prime-agent`` kind.
check_prime_auth = check_prime_agent_auth


def check_gemini_auth(cfg: ServiceConfig) -> CheckResult:
    """Catch Gemini CLI noninteractive auth failures before dispatch."""
    name = "agent.kind=gemini.auth"
    if cfg.agent.kind != "gemini":
        return CheckResult(name, "pass", "not gemini (skipped)")

    settings = Path.home() / ".gemini" / "settings.json"
    selected_auth_type: object = None
    nested_hint = ""
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return CheckResult(name, "fail", f"cannot read {settings}: {exc}")
        selected_auth_type = data.get("selectedAuthType")
        security = data.get("security")
        if isinstance(security, dict):
            auth = security.get("auth")
            if isinstance(auth, dict) and auth.get("selectedType"):
                nested_hint = (
                    " Found security.auth.selectedType, but this Gemini CLI"
                    " reads selectedAuthType for noninteractive runs."
                )

    gemini_api_key = bool(os.environ.get("GEMINI_API_KEY"))
    if not isinstance(selected_auth_type, str) or not selected_auth_type.strip():
        if gemini_api_key:
            return CheckResult(
                name,
                "pass",
                "GEMINI_API_KEY present; Gemini CLI will use gemini-api-key",
            )
        return CheckResult(
            name,
            "fail",
            f"{settings} lacks root selectedAuthType and GEMINI_API_KEY is unset."
            f"{nested_hint} Run `gemini` and `/auth`, or export GEMINI_API_KEY.",
        )

    auth_type = selected_auth_type.strip()
    if auth_type == "oauth-personal":
        return CheckResult(name, "pass", f"{settings} selectedAuthType={auth_type}")
    if auth_type == "gemini-api-key":
        if gemini_api_key:
            return CheckResult(name, "pass", "GEMINI_API_KEY present")
        return CheckResult(
            name,
            "fail",
            f"{settings} selects gemini-api-key but GEMINI_API_KEY is unset",
        )
    if auth_type == "vertex-ai":
        has_vertex_project_location = bool(
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            and os.environ.get("GOOGLE_CLOUD_LOCATION")
        )
        if has_vertex_project_location or os.environ.get("GOOGLE_API_KEY"):
            return CheckResult(name, "pass", "Vertex AI auth env is present")
        return CheckResult(
            name,
            "fail",
            f"{settings} selects vertex-ai but Vertex AI env vars are unset",
        )
    return CheckResult(name, "fail", f"unsupported Gemini selectedAuthType={auth_type!r}")


def check_kiro_auth(cfg: ServiceConfig) -> CheckResult:
    """Catch Kiro CLI noninteractive auth failures before dispatch."""
    name = "agent.kind=kiro.auth"
    if cfg.agent.kind != "kiro":
        return CheckResult(name, "pass", "not kiro (skipped)")
    if os.environ.get("KIRO_API_KEY"):
        return CheckResult(name, "pass", "KIRO_API_KEY present")
    whoami = _kiro_whoami()
    if whoami.status == "pass":
        return whoami
    return CheckResult(
        name,
        "fail",
        "KIRO_API_KEY is unset and `kiro-cli whoami` did not confirm a login; "
        "run `kiro-cli login` or export KIRO_API_KEY before dispatch.",
    )


def _kiro_whoami() -> CheckResult:
    name = "agent.kind=kiro.auth"
    try:
        completed = subprocess.run(
            ["kiro-cli", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, "fail", f"`kiro-cli whoami` failed: {exc}")
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode == 0 and "Logged in" in output:
        return CheckResult(name, "pass", "`kiro-cli whoami` confirms login")
    suffix = f": {output}" if output else ""
    return CheckResult(name, "fail", f"`kiro-cli whoami` returned {completed.returncode}{suffix}")


def check_agy_state_dir(cfg: ServiceConfig) -> CheckResult:
    """Catch AGY sandbox/home write failures before the first worker turn."""
    name = "agent.kind=agy.state"
    if cfg.agent.kind != "agy":
        return CheckResult(name, "pass", "not agy (skipped)")

    state_dir = Path.home() / ".gemini" / "antigravity-cli"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(name, "fail", f"cannot create {state_dir}: {exc}")
    try:
        with tempfile.NamedTemporaryFile(
            dir=state_dir,
            prefix=".symphony-doctor-",
            delete=True,
        ):
            pass
    except OSError as exc:
        return CheckResult(
            name,
            "fail",
            f"{state_dir} is not writable; AGY requires writable CLI state: {exc}",
        )
    return CheckResult(name, "pass", f"{state_dir} is writable")


def check_after_create_hook(cfg: ServiceConfig) -> CheckResult:
    hook = cfg.hooks.after_create or ""
    if not hook.strip():
        return CheckResult("hooks.after_create", "pass", "empty (skipped at runtime)")
    for token in _PLACEHOLDER_TOKENS:
        if token in hook:
            return CheckResult(
                "hooks.after_create",
                "fail",
                f"contains placeholder {token!r} — every dispatch will fail with rc=128. "
                "Switch to the worktree default (see WORKFLOW.file.example.md) "
                "or replace with a real clone target / `: noop`.",
            )
    masked_lines = _warning_after_create_lines(cfg, hook)
    if masked_lines:
        sample = "; ".join(masked_lines[:2])
        suffix = "" if len(masked_lines) <= 2 else f"; +{len(masked_lines) - 2} more"
        status: Status = "fail" if cfg.hooks.fail_on_warning_patterns else "warn"
        policy = (
            " hooks.fail_on_warning_patterns is true."
            if cfg.hooks.fail_on_warning_patterns
            else ""
        )
        return CheckResult(
            "hooks.after_create",
            status,
            "setup command may hide failures or contain known setup failure text: "
            f"{sample}{suffix}.{policy}",
        )
    return CheckResult("hooks.after_create", "pass", "looks customized")


_HOOK_SCRIPT_RE = re.compile(r"[\w./$@{}-]*\.sh\b")


def _hook_script_text(cfg: ServiceConfig, hook: str) -> str:
    """Hook text plus the bodies of any `*.sh` scripts it invokes.

    The shipped `after_create` is one line (`bash "$SYMPHONY_WORKFLOW_DIR/
    scripts/symphony-setup-worktree.sh"`), so grepping the hook alone tells
    us nothing about which directories it links.
    """
    parts = [hook]
    workflow_dir = cfg.workflow_path.parent
    for raw in _HOOK_SCRIPT_RE.findall(hook):
        relative = raw.replace("$SYMPHONY_WORKFLOW_DIR", "").replace(
            "${SYMPHONY_WORKFLOW_DIR}", ""
        )
        candidate = (workflow_dir / relative.lstrip("/")).resolve()
        try:
            if candidate.is_file():
                parts.append(candidate.read_text(encoding="utf-8"))
        except OSError:  # pragma: no cover - unreadable script
            continue
    return "\n".join(parts)


def check_board_reachable_from_workspace(cfg: ServiceConfig) -> CheckResult:
    """Can a dispatched worker write to the *host* board from its workspace?

    Acceptance finding 1: the shipped setup hook used to link a directory
    literally named `kanban`. On any other `tracker.board_root` the worker
    silently got a private board copy, its state transitions never reached
    the orchestrator, and the ticket was re-dispatched forever. Static check
    only — it greps the hook and the scripts the hook runs.
    """
    name = "board.reachable"
    if cfg.tracker.kind != "file":
        return CheckResult(name, "pass", f"tracker.kind={cfg.tracker.kind} (skipped)")
    root = cfg.tracker.board_root
    if root is None:
        return CheckResult(name, "fail", "file tracker has no board_root")
    if not root.exists():
        return CheckResult(
            name, "fail", f"{root} does not exist — run `symphony board init {root}`"
        )
    workflow_dir = cfg.workflow_path.parent.resolve()
    try:
        board_name = root.resolve().relative_to(workflow_dir).as_posix()
    except ValueError:
        return CheckResult(
            name, "pass", f"{root} is outside {workflow_dir}; workers use the host path"
        )
    hook = cfg.hooks.after_create or ""
    if not hook.strip():
        return CheckResult(
            name, "pass", "no after_create hook; workspace is not a worktree"
        )
    text = _hook_script_text(cfg, hook)
    if "SYMPHONY_BOARD_ROOT_NAME" in text or re.search(
        rf"(?<![\w/-]){re.escape(board_name)}(?![\w-])", text
    ):
        return CheckResult(name, "pass", f"after_create links {board_name}/")
    return CheckResult(
        name,
        "warn",
        f"after_create never mentions the board root {board_name!r} — workers "
        "may get a private board copy and the ticket will be re-dispatched "
        "forever. Use ${SYMPHONY_BOARD_ROOT_NAME:-kanban} in the link loop.",
    )

def check_deep_preset_merge_contract(cfg: ServiceConfig) -> CheckResult:
    """Deep-preset boards need a coherent single-merge branch policy.

    Every deep lane is its own ticket, hence its own worktree on its own
    `symphony/<ID>` branch. QA/Verify/Document can only see a Build slice
    that already merged, so the preset requires `auto_merge_on_done` plus a
    feature base that resolves to the merge target. Without it, either
    unverified work never reaches the downstream lanes, or they re-prove a
    tree that does not contain the code.
    """
    name = "board.deep_merge_contract"
    if guess_lane_preset(cfg.tracker.active_states) != "deep":
        return CheckResult(name, "pass", "not a deep-preset board (skipped)")
    if not cfg.agent.auto_merge_on_done:
        return CheckResult(
            name,
            "fail",
            "deep preset requires agent.auto_merge_on_done: true — Build "
            "slices never reach the QA/Verify/Document worktrees otherwise",
        )
    base = (cfg.agent.feature_base_branch or "").strip()
    target = (cfg.agent.auto_merge_target_branch or "").strip()
    if base != target:
        return CheckResult(
            name,
            "fail",
            f"deep preset requires feature_base_branch ({base or '<current>'}) "
            f"== auto_merge_target_branch ({target or '<current>'}) — new "
            "worktrees must start from the branch the merges land on",
        )
    return CheckResult(
        name,
        "pass",
        f"auto_merge_on_done on, base == target ({base or '<current branch>'})",
    )

def check_stage_contracts(cfg: ServiceConfig) -> CheckResult:
    """Report whether the mechanical evidence floor runs on this board.

    F-06: with `agent.stage_contracts: auto` (the default), renaming any
    default lane turns the whole stage-contract validator off. That is a
    legal outcome of a customized board, but it silently removes the
    product's evidence floor — so it gets a row here.
    """
    from ..orchestrator.contracts import board_uses_default_contracts

    name = "agent.stage_contracts"
    mode = (cfg.agent.stage_contracts or "auto").strip().lower()
    enabled = cfg.agent.stage_contracts_enabled(cfg.tracker.active_states)
    if enabled:
        return CheckResult(name, "pass", f"{mode}: contracts enforced")
    if mode == "off":
        return CheckResult(
            name, "warn", "off: no mechanical evidence gate; prompts are the only gate"
        )
    offending = [
        state
        for state in cfg.tracker.active_states
        if not board_uses_default_contracts((state,))
    ]
    return CheckResult(
        name,
        "warn",
        "auto: contracts disabled because these lanes are not default-preset "
        f"lanes ({', '.join(offending) or 'n/a'}) — set agent.stage_contracts: "
        "on to enforce them anyway",
    )

def check_symphony_cli_reachable(cfg: ServiceConfig) -> CheckResult:
    """Can a dispatched worker actually run `symphony board ...`?

    F-19: the stage prompts and the chat preamble now *require* the board
    CLI, but Symphony is usually installed in a venv and launched by
    absolute path, so `symphony` need not be on the worker's PATH. The
    orchestrator exports `SYMPHONY_CLI`, and the prompts use
    `${SYMPHONY_CLI:-symphony}`; this check reports both halves.
    """
    from ..orchestrator.helpers import resolve_symphony_cli

    name = "board.cli"
    if cfg.tracker.kind != "file":
        return CheckResult(name, "pass", f"tracker.kind={cfg.tracker.kind} (skipped)")
    resolved = resolve_symphony_cli()
    bash = resolve_bash()
    on_path = False
    try:
        probe = subprocess.run(
            [bash, "-lc", "command -v symphony"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        on_path = probe.returncode == 0 and bool(probe.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        on_path = False
    if on_path:
        return CheckResult(name, "pass", f"`symphony` on the worker PATH; {resolved}")
    if " -m " in resolved:
        return CheckResult(
            name,
            "fail",
            "`symphony` is not on a login-shell PATH and no console script was "
            "found — prompts that call the board CLI will fail. Install the "
            "package (`pip install -e .`) or add its venv bin to PATH.",
        )
    return CheckResult(
        name,
        "warn",
        f"`symphony` is not on a login-shell PATH; workers get SYMPHONY_CLI="
        f"{resolved}. Prompts must use ${{SYMPHONY_CLI:-symphony}} (the shipped "
        "ones do); custom prompts calling bare `symphony` will fail.",
    )

def check_board_dependencies(cfg: ServiceConfig) -> CheckResult:
    """Report dangling `blocked_by` ids and dependency cycles on the board.

    F-13: a blocker id that does not exist (agent typo, deleted ticket)
    never resolves, so the ticket is silently never dispatched again. The
    only previous signal was one WARN line in the orchestrator log.
    """
    from ..trackers.file import FileBoardTracker
    from ..trackers.validate import board_edges, dangling_blockers, find_cycle

    name = "board.dependencies"
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return CheckResult(name, "pass", f"tracker.kind={cfg.tracker.kind} (skipped)")
    if not cfg.tracker.board_root.exists():
        return CheckResult(name, "pass", "no board directory yet")
    try:
        issues = FileBoardTracker(cfg.tracker).scan_all()
    except SymphonyError as exc:
        return CheckResult(name, "warn", f"could not scan the board: {exc}")
    dangling = dangling_blockers(issues)
    cycle = find_cycle(board_edges(issues))
    problems: list[str] = []
    for identifier, missing in sorted(dangling.items()):
        problems.append(f"{identifier} blocked_by {', '.join(missing)} (not on board)")
    if cycle:
        problems.append(f"cycle: {' -> '.join(cycle)}")
    if not problems:
        return CheckResult(
            name, "pass", f"{len(issues)} ticket(s), no dangling blockers or cycles"
        )
    sample = "; ".join(problems[:3])
    suffix = "" if len(problems) <= 3 else f"; +{len(problems) - 3} more"
    return CheckResult(
        name,
        "fail",
        f"{sample}{suffix} — these tickets will never be dispatched; fix with "
        "`symphony board update <ID> --blocked-by <ID>`",
    )

def check_prompts(cfg: ServiceConfig) -> CheckResult:
    paths = []
    if cfg.prompts.base_path is not None:
        paths.append(cfg.prompts.base_path)
    paths.extend(cfg.prompts.stage_paths.values())
    if not paths:
        return CheckResult("prompts", "pass", "built-in template in use")
    sample = ", ".join(str(p) for p in paths[:3])
    suffix = "" if len(paths) <= 3 else f", +{len(paths) - 3} more"
    return CheckResult(
        "prompts.files",
        "pass",
        f"{len(paths)} prompt file{'s' if len(paths) != 1 else ''}: {sample}{suffix}",
    )


def check_workspace_root(cfg: ServiceConfig) -> CheckResult:
    root = cfg.workspace_root
    name = f"workspace.root={root}"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(name, "fail", f"cannot create {root} — {exc}")
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".symphony-doctor-", delete=True):
            pass
    except OSError as exc:
        return CheckResult(name, "fail", f"not writable — {exc}")
    return CheckResult(name, "pass", f"{root} exists and is writable")


def check_git_history_writable(cfg: ServiceConfig) -> CheckResult:
    """The host must be able to write git objects — the Final History Gate needs it.

    Symphony records every ticket's delivery commit from the orchestrator
    process precisely because a sandboxed agent may not reach the object
    database. That safety net only holds if the *host* can write there, so
    probe it for real rather than assuming.

    Also reports the git roots a sandboxed agent will be granted. With the
    default worktree workspace these sit outside the workspace directory, and
    an agent that is not granted them fails `git add` with
    `failed to insert into database`.
    """
    repo = cfg.workflow_path.parent
    name = "git history writable"
    common_dir = resolve_git_common_dir(repo)
    if common_dir is None:
        return CheckResult(
            name,
            "warn",
            f"{repo} is not a git repository — ticket branches and the "
            "delivery-commit gate are unavailable",
        )
    objects_dir = common_dir / "objects"
    try:
        objects_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=objects_dir, prefix=".symphony-doctor-", delete=True
        ):
            pass
    except OSError as exc:
        return CheckResult(
            name,
            "fail",
            f"cannot write {objects_dir} — {exc}; the host cannot record "
            "delivery commits, so finished tickets will stall",
        )
    roots = writable_git_roots(repo)
    detail = f"{objects_dir} is writable"
    if len(roots) > 1:
        detail = f"{detail}; agents also need {', '.join(roots)}"
    return CheckResult(name, "pass", detail)


# Every supported agent kind and the config field holding its command. Kept
# exhaustive on purpose: a new backend that Symphony can launch but cannot
# grant git roots to would reintroduce the sandbox block silently.
def _agent_commands(cfg: ServiceConfig) -> dict[str, str]:
    return {
        "agy": cfg.agy.command,
        "claude": cfg.claude.command,
        "codex": cfg.codex.command,
        "gemini": cfg.gemini.command,
        "kiro": cfg.kiro.command,
        "opencode": cfg.opencode.command,
        "pi": cfg.pi.command,
        "prime-agent": cfg.prime_agent.command,
    }

# CLIs Symphony can widen on the command line. Every other kind gets the grant
# through the environment only, which is all those CLIs can consume today.
_CLI_FLAG_INJECTORS = ("codex", "claude")


def check_agent_git_grant(cfg: ServiceConfig) -> CheckResult:
    """Whether the configured agent will actually receive its git roots.

    All backends export ``SYMPHONY_GIT_WRITABLE_ROOTS``. codex and claude
    additionally get the roots injected as CLI flags, but only when Symphony
    can see the literal CLI token at the front of the command — a wrapper
    script hides it, and then the wrapper has to forward the env var itself
    or the agent is back to the sandbox that produced `Operation not
    permitted`.
    """
    name = "agent git grant"
    kind = cfg.agent.kind
    command = _agent_commands(cfg).get(kind, "").strip()
    if kind not in _CLI_FLAG_INJECTORS:
        return CheckResult(
            name,
            "pass",
            f"{kind} receives git roots via $SYMPHONY_GIT_WRITABLE_ROOTS",
        )
    if kind == "codex" and not _sandbox_uses_workspace_write(
        cfg.codex.thread_sandbox, cfg.codex.turn_sandbox_policy
    ):
        return CheckResult(
            name, "pass", "codex sandbox is not workspace-write; no grant needed"
        )
    if command == kind or command.startswith((f"{kind} ", f"{kind}\t")):
        return CheckResult(name, "pass", f"Symphony injects git roots into `{kind}`")
    env_var = (
        "SYMPHONY_CODEX_WRITABLE_ROOTS"
        if kind == "codex"
        else "SYMPHONY_GIT_WRITABLE_ROOTS"
    )
    return CheckResult(
        name,
        "warn",
        f"{kind}.command is a wrapper script, so Symphony cannot inject git "
        f"roots on the command line; forward ${env_var} from the wrapper",
    )


def check_tracker(cfg: ServiceConfig) -> CheckResult:
    tracker = cfg.tracker
    if tracker.kind == "file":
        root = tracker.board_root
        if root is None:
            return CheckResult("tracker.board_root", "fail", "file tracker has no board_root")
        if not root.exists():
            return CheckResult(
                "tracker.board_root",
                "fail",
                f"{root} does not exist — run `symphony board init {root}`",
            )
        ticket_count = sum(1 for _ in root.glob("*.md"))
        return CheckResult(
            "tracker.board_root",
            "pass",
            f"{root} ({ticket_count} ticket{'s' if ticket_count != 1 else ''})",
        )
    if tracker.kind == "linear":
        if not tracker.api_key:
            return CheckResult(
                "tracker.api_key",
                "fail",
                "linear tracker requires api_key (set $LINEAR_API_KEY or hardcode)",
            )
        if tracker.api_key.startswith("$"):
            env_name = tracker.api_key.lstrip("$")
            if not os.environ.get(env_name):
                return CheckResult(
                    "tracker.api_key",
                    "fail",
                    f"api_key references ${env_name} but the env var is unset",
                )
        return CheckResult("tracker.api_key", "pass", "api_key present")
    return CheckResult(f"tracker.kind={tracker.kind}", "warn", "unknown tracker kind")


def check_shell() -> CheckResult:
    """Hooks and backend subprocesses spawn via ``bash -lc``. On Windows we
    must avoid the WSL launcher (``C:\\Windows\\System32\\bash.exe``) — see
    ``_shell.resolve_bash``. On macOS/Linux we still verify ``bash`` is
    actually on ``$PATH`` so minimal containers and nix-shells fail loudly
    here rather than silently at first dispatch."""
    bash = resolve_bash()
    # If ``bash`` is a bare name (e.g. "bash" or "wsl"), resolve via PATH so
    # WSL-launcher detection sees the actual binary.
    resolved = bash if os.path.isfile(bash) else (shutil.which(bash) or bash)

    if _IS_WIN32 and _is_wsl_launcher(resolved):
        return CheckResult(
            "shell.bash",
            "fail",
            f"{resolved} is the WSL launcher — install Git for Windows "
            "or set $SYMPHONY_BASH to a Git Bash binary",
        )

    if not (os.path.isfile(bash) or shutil.which(bash)):
        if _IS_WIN32:
            return CheckResult(
                "shell.bash",
                "fail",
                "no usable bash found — install Git for Windows or set $SYMPHONY_BASH",
            )
        return CheckResult(
            "shell.bash",
            "fail",
            f"{bash!r} not found on $PATH — install bash or set $SYMPHONY_BASH",
        )

    return CheckResult("shell.bash", "pass", bash)


def check_stage_turn_budget(cfg: ServiceConfig) -> CheckResult:
    error = stage_turn_budget_error(cfg)
    if error is not None:
        return CheckResult("agent.max_turns", "fail", error)
    active_count = len([state for state in cfg.tracker.active_states if state])
    return CheckResult(
        "agent.max_turns",
        "pass",
        f"{cfg.agent.max_turns} turn budget covers {active_count} active states",
    )


def check_workflow_registry(cfg: ServiceConfig) -> CheckResult:
    """Confirm `.symphony/state.db` is migrated to the current schema."""
    from ..orchestrator.migrations import LATEST_SCHEMA_VERSION
    from ..orchestrator.run_registry import RunRegistry, registry_path_for_workflow

    path = registry_path_for_workflow(cfg.workflow_path)
    if not path.exists():
        return CheckResult(
            "state.db", "pass", "no run history yet; will be created on first run"
        )
    try:
        registry = RunRegistry(path)
    except Exception as exc:
        return CheckResult("state.db", "fail", f"cannot open {path}: {exc}")
    try:
        version = registry.schema_version()
        applied = registry.applied_migrations
    finally:
        registry.close()
    if version < LATEST_SCHEMA_VERSION:
        return CheckResult(
            "state.db",
            "fail",
            f"schema version {version} < {LATEST_SCHEMA_VERSION}; migration "
            "did not complete",
        )
    detail = f"schema v{version}"
    if applied:
        detail += f" (applied {', '.join(str(v) for v in applied)} just now)"
    return CheckResult("state.db", "pass", detail)


def check_source_repository(cfg: ServiceConfig) -> CheckResult:
    """Refuse workflows in the checkout that supplies Symphony's runtime."""
    if workflow_uses_protected_source_repo(cfg.workflow_path):
        return CheckResult(
            "workflow.repository", "fail", PROTECTED_REPOSITORY_MESSAGE
        )
    return CheckResult(
        "workflow.repository", "pass", "project repository is separate from Symphony source"
    )


def check_app_release_contract(cfg: ServiceConfig) -> CheckResult:
    """Validate the shared contract only when a local board opts into app delivery."""
    name = "app.release-contract"
    if cfg.tracker.kind != "file":
        return CheckResult(
            name,
            "pass",
            "remote board labels are not inspected; app release delivery is not "
            "enabled by local configuration, and a labeled runtime transition "
            "will fail closed because repair cycles require tracker.kind=file",
        )
    try:
        tracker = FileBoardTracker(cfg.tracker)
        try:
            states = tuple(
                dict.fromkeys(
                    (*cfg.tracker.active_states, *cfg.tracker.terminal_states, "Verify")
                )
            )
            issues = tracker.fetch_issues_by_states(states)
        finally:
            tracker.close()
    except Exception as exc:
        return CheckResult(name, "fail", f"cannot inspect local app-release labels: {exc}")
    release_enabled = any(_has_app_release_label(issue.labels) for issue in issues)
    if not release_enabled:
        from ..orchestrator.run_registry import (
            RunRegistry,
            registry_path_for_workflow,
        )

        try:
            registry = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
            try:
                release_enabled = registry.has_release_authority()
            finally:
                registry.close()
        except Exception as exc:
            return CheckResult(
                name,
                "fail",
                f"cannot inspect durable app-release authority: {exc}",
            )
    if not release_enabled:
        return CheckResult(name, "pass", "app release delivery is not enabled on this board")
    if not any(
        normalize_state(state) == "verify" for state in cfg.tracker.active_states
    ):
        return CheckResult(
            name,
            "fail",
            "app release delivery requires an active Verify lane; fresh evidence "
            "cannot be collected or rewound safely without one",
        )
    if not has_release_finalizer_lane(cfg):
        return CheckResult(
            name,
            "fail",
            "app release delivery requires a non-Verify active lane for the "
            "host-controlled finalizer",
        )
    if not has_release_success_terminal(cfg):
        return CheckResult(
            name,
            "fail",
            "app release delivery requires an explicit successful terminal lane "
            "such as Done or Completed; ambiguous terminals cannot authorize delivery",
        )

    contract_path = cfg.workflow_path.parent / "release-contract.yaml"
    errors = inspect_release_contract(
        contract_path,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
    )
    if errors:
        return CheckResult(name, "fail", "; ".join(errors))
    return CheckResult(
        name,
        "pass",
        "release-contract.yaml is valid; atomic file-tracker lifecycle is available",
    )


def _has_app_release_label(labels: Iterable[str]) -> bool:
    return any(label.strip().lower() == "app-release" for label in labels)


def run_checks(cfg: ServiceConfig, host: str = "127.0.0.1") -> list[CheckResult]:
    return [
        check_source_repository(cfg),
        check_port(cfg, host=host),
        check_api_token_env(cfg),
        check_shell(),
        check_stage_turn_budget(cfg),
        check_agent_cli(cfg),
        check_pi_auth(cfg),
        check_prime_agent_auth(cfg),
        check_gemini_auth(cfg),
        check_agy_state_dir(cfg),
        check_kiro_auth(cfg),
        check_prompts(cfg),
        check_after_create_hook(cfg),
        check_workspace_root(cfg),
        check_git_history_writable(cfg),
        check_agent_git_grant(cfg),
        check_tracker(cfg),
        check_board_reachable_from_workspace(cfg),
        check_deep_preset_merge_contract(cfg),
        check_stage_contracts(cfg),
        check_app_release_contract(cfg),
        check_symphony_cli_reachable(cfg),
        check_board_dependencies(cfg),
        check_workflow_registry(cfg),
    ]


_STATUS_ICON: dict[Status, str] = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
_STATUS_COLOR: dict[Status, str] = {
    "pass": "\033[32m",  # green
    "warn": "\033[33m",  # yellow
    "fail": "\033[31m",  # red
}
_RESET = "\033[0m"


def format_results(results: Iterable[CheckResult], *, color: bool = False) -> str:
    lines: list[str] = []
    for r in results:
        icon = _STATUS_ICON[r.status]
        if color:
            icon = f"{_STATUS_COLOR[r.status]}{icon}{_RESET}"
        lines.append(f"{icon}  {r.name:<28}  {r.message}")
    return "\n".join(lines)


def _exit_code(results: Iterable[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="symphony doctor",
        description="Preflight checks for WORKFLOW.md before launching symphony.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to test the JSON API port against (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI color even when stdout is a tty",
    )
    args = parser.parse_args(argv)

    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.exists():
        print(f"FAIL  workflow file not found: {workflow_path}", file=sys.stderr)
        return 2

    try:
        cfg = build_service_config(load_workflow(workflow_path))
    except SymphonyError as exc:
        print(f"FAIL  workflow load failed: {exc}", file=sys.stderr)
        return 2

    color = (not args.no_color) and sys.stdout.isatty()
    results = run_checks(cfg, host=args.host)
    print(format_results(results, color=color))
    return _exit_code(results)
