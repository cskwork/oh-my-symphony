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
from dataclasses import replace
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
from ..errors import SymphonyError
from ..service import ProcessRunningPredicate, port_owner_hint
from ..workflow import (
    ServiceConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)
from ..workflow.preflight import stage_turn_budget_error


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
    else:
        return CheckResult(f"agent.kind={kind}", "fail", f"unsupported agent kind {kind!r}")

    name = f"agent.kind={kind}"
    try:
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


def check_board_viewer(cfg: ServiceConfig) -> CheckResult:
    """Warn when the web HTML viewer script is absent.

    `symphony service start --viewer-port N` silently skips the viewer if
    `<workflow-dir>/tools/board-viewer/server.py` is missing (see
    `service.board_viewer_script_for`). Operators routinely don't notice the
    omitted "started board viewer" line and end up with `viewer_pid: null`
    in the run record. This is a WARN (not FAIL) because the orchestrator
    runs fine without the viewer.
    """
    script = cfg.workflow_path.parent / "tools" / "board-viewer" / "server.py"
    if script.exists():
        return CheckResult("viewer.board-viewer", "pass", f"{script}")
    return CheckResult(
        "viewer.board-viewer",
        "warn",
        (
            f"{script} not found — `--viewer-port` will be a no-op. "
            "The built-in web app on the orchestrator port (`--port`, default "
            "9999) replaces it; the legacy `tools/board-viewer/` copy is only "
            "needed for the separate read-only viewer."
        ),
    )


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


def run_checks(cfg: ServiceConfig, host: str = "127.0.0.1") -> list[CheckResult]:
    return [
        check_port(cfg, host=host),
        check_shell(),
        check_stage_turn_budget(cfg),
        check_agent_cli(cfg),
        check_pi_auth(cfg),
        check_gemini_auth(cfg),
        check_agy_state_dir(cfg),
        check_kiro_auth(cfg),
        check_prompts(cfg),
        check_after_create_hook(cfg),
        check_workspace_root(cfg),
        check_tracker(cfg),
        check_board_viewer(cfg),
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
        "--port",
        type=int,
        default=None,
        help="override the workflow server port for this preflight",
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
    if args.port is not None:
        cfg = replace(cfg, server=replace(cfg.server, port=args.port))

    color = (not args.no_color) and sys.stdout.isatty()
    results = run_checks(cfg, host=args.host)
    print(format_results(results, color=color))
    return _exit_code(results)
