"""CLI entry point.

Subcommands:
    symphony [WORKFLOW]            run orchestrator (optionally with HTTP API via --port)
    symphony tui [WORKFLOW]        run orchestrator + Jira-style CLI Kanban TUI
    symphony board ...             file-tracker board helper
    symphony doctor [WORKFLOW]     preflight checks for WORKFLOW.md
    symphony service ...           managed background orchestrator + viewer
    symphony wiki-sweep ...        scan docs/llm-wiki/ for dup/orphan/stale rows
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from .. import __version__
from ..errors import SymphonyError
from ..utils import wiki_sweep as wiki_sweep
from ..utils.keep_awake import KeepAwake
from ..logging import configure_logging
from ..orchestrator import Orchestrator
from ..orchestrator.run_registry import (
    RunRecord,
    RunRegistry,
    clamp_run_history_limit,
    registry_path_for_workflow,
)
from ..progress_md import ProgressFileWriter
from ..server import build_app, run_server
from ..service import port_owner_hint
from ..workflow import WorkflowState, resolve_workflow_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony",
        description="Symphony multi-agent — Codex / Claude Code / Gemini orchestration.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"symphony {__version__}",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="enable HTTP JSON API on this port (overrides server.port)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host for HTTP API (default: loopback)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="log level: DEBUG, INFO, WARN, ERROR",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="launch the Jira-style CLI Kanban board (same as `symphony tui ...`)",
    )
    parser.add_argument(
        "--progress-md",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "mirror live progress to WORKFLOW-PROGRESS.md (default: on). "
            "Pass --no-progress-md to disable. Path can be set via "
            "--progress-md-path or `progress.path` in WORKFLOW.md."
        ),
    )
    parser.add_argument(
        "--progress-md-path",
        default=None,
        help="override WORKFLOW-PROGRESS.md location (relative to CWD)",
    )
    parser.add_argument(
        "--keep-awake",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "prevent host sleep/screen lock while running (macOS only; "
            "no-op elsewhere). Defaults to on; pass --no-keep-awake to "
            "disable, or set `system.keep_awake: false` in WORKFLOW.md."
        ),
    )
    return parser


def _fail_startup(log, event: str, sentence: str, **fields) -> int:
    """U3 — structured event for logs plus one actionable sentence.

    The headless run path used to emit only key=value events
    (`workflow_load_failed error=...`), which read as machine noise next
    to the friendly sentences the service path prints for the same
    failures.
    """
    log.error(event, **fields)
    print(f"symphony: {sentence}", file=sys.stderr)
    return 1


def _startup_preflight_failures(cfg) -> list[str]:
    """A2 — doctor-lite before the orchestrator starts.

    Only the checks whose failure is otherwise deferred and cryptic: a
    missing agent CLI fails at first dispatch, the shipped after_create
    placeholder fails every dispatch with rc=128. The full check set
    stays in `symphony doctor`.
    """
    from .doctor import check_after_create_hook, check_agent_cli

    failures: list[str] = []
    for check in (check_agent_cli(cfg), check_after_create_hook(cfg)):
        if check.status == "fail":
            failures.append(f"{check.name}: {check.message}")
    return failures


async def _run(args: argparse.Namespace) -> int:
    log = configure_logging(args.log_level)

    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.exists():
        return _fail_startup(
            log,
            "workflow_path_missing",
            f"WORKFLOW.md not found at {workflow_path}. Pass a path "
            "(`symphony ./WORKFLOW.md`) or run `symphony board init` to "
            "scaffold a new board.",
            path=str(workflow_path),
        )

    state = WorkflowState(workflow_path)
    cfg, err = state.reload()
    if cfg is None:
        return _fail_startup(
            log,
            "workflow_load_failed",
            f"WORKFLOW.md could not be loaded: {err}. Fix the YAML "
            f"frontmatter and re-run; `symphony doctor {workflow_path}` "
            "pinpoints most configuration mistakes.",
            error=str(err),
        )

    if args.tui and not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _fail_startup(
            log,
            "tui_requires_tty",
            "the TUI needs an interactive terminal. Run without --tui for "
            "headless mode, or use `symphony service start` and open the "
            "web board instead.",
        )

    preflight_failures = _startup_preflight_failures(cfg)
    if preflight_failures:
        for failure in preflight_failures:
            print(f"symphony: {failure}", file=sys.stderr)
        return _fail_startup(
            log,
            "startup_preflight_failed",
            f"fix the above and re-run; `symphony doctor {workflow_path}` "
            "runs the full check set.",
            failures=len(preflight_failures),
        )

    # CLI flag wins over WORKFLOW.md; both default to on for the macOS
    # "lock the screen on me and I lose the run" case the user flagged.
    keep_awake_enabled = (
        args.keep_awake if args.keep_awake is not None else cfg.system.keep_awake
    )
    keep_awake = KeepAwake() if keep_awake_enabled else None
    if keep_awake is not None:
        keep_awake.start()

    orchestrator = Orchestrator(state)
    try:
        await orchestrator.start()
    except SymphonyError as exc:
        if keep_awake is not None:
            keep_awake.stop()
        return _fail_startup(
            log,
            "startup_failed",
            f"orchestrator startup failed: {exc}. "
            f"`symphony doctor {workflow_path}` checks ports, the agent "
            "CLI, and tracker configuration.",
            error=str(exc),
        )

    # Register the progress writer BEFORE any subsequent `await` so the
    # first tick's `_notify_observers` sees it. CLI flag > WORKFLOW.md
    # `progress.enabled` > default True.
    progress_enabled = (
        args.progress_md if args.progress_md is not None else cfg.progress.enabled
    )
    if progress_enabled:
        if args.progress_md_path:
            override = Path(args.progress_md_path)
            progress_path = override if override.is_absolute() else (Path.cwd() / override).resolve()
        elif cfg.progress.path is not None:
            progress_path = cfg.progress.path
        else:
            progress_path = (workflow_path.parent / "WORKFLOW-PROGRESS.md").resolve()
        progress_writer = ProgressFileWriter(
            orchestrator,
            state,
            progress_path,
            max_transitions=cfg.progress.max_transitions,
        )
        progress_writer.register()
        log.info("progress_md_active", path=str(progress_path))

    server_port = args.port if args.port is not None else cfg.server.port
    runner = None
    if server_port is not None:
        app = build_app(orchestrator)
        try:
            runner, bound = await run_server(app, args.host, server_port)
        except OSError as exc:
            # A2 — EADDRINUSE used to escape as a raw traceback after the
            # orchestrator had already started.
            await orchestrator.stop()
            if keep_awake is not None:
                keep_awake.stop()
            owner = port_owner_hint(workflow_path, server_port)
            owner_sentence = (
                f" {owner}."
                if owner
                else " Stop the other process "
                f"(`lsof -ti :{server_port}`), pick a different --port, or "
                "check `symphony service status`."
            )
            return _fail_startup(
                log,
                "http_port_unavailable",
                f"port {server_port} on {args.host} is already in use or "
                f"cannot be bound ({exc}).{owner_sentence}",
                host=args.host,
                port=server_port,
                error=str(exc),
            )
        log.info("http_extension_active", host=args.host, port=bound)
        print(
            f"symphony: board ready at http://{args.host}:{bound}",
            file=sys.stderr,
        )
    else:
        # Surfaces the silent-no-HTTP case so the operator immediately sees
        # why board-viewer / API consumers can't reach this instance.
        # Common cause: `server.port` lives outside frontmatter (embedded
        # `---` fence in a YAML literal truncated the workflow header) —
        # see workflow.parse_workflow_text greedy end detection.
        log.info(
            "http_extension_disabled",
            reason="no server.port in WORKFLOW.md frontmatter",
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass  # Windows / restricted env

    tui_task: asyncio.Task[None] | None = None
    if args.tui:
        from symphony.tui import KanbanTUI

        tui = KanbanTUI(orchestrator, state)

        async def _tui_runner() -> None:
            try:
                await tui.run()
            finally:
                stop_event.set()

        tui_task = asyncio.create_task(_tui_runner(), name="symphony-tui")

    try:
        await stop_event.wait()
    finally:
        log.info("shutdown_initiated")
        if tui_task is not None:
            tui_task.cancel()
            try:
                await tui_task
            except (asyncio.CancelledError, Exception):
                pass
        await orchestrator.stop()
        if runner is not None:
            await runner.cleanup()
        if keep_awake is not None:
            keep_awake.stop()
        log.info("shutdown_complete")
    return 0


def _wiki_sweep_main(argv: list[str]) -> int:
    """`symphony wiki-sweep` — pure subcommand. No orchestrator boot."""
    _wiki_sweep = wiki_sweep

    parser = argparse.ArgumentParser(
        prog="symphony wiki-sweep",
        description=(
            "Scan a docs/llm-wiki/ tree for duplicate slugs, INDEX↔file "
            "orphans, missing files, and entries older than "
            f"{_wiki_sweep.STALE_AFTER_DAYS} days. Non-zero exit if any "
            "duplicate / orphan / missing-file is found."
        ),
    )
    parser.add_argument(
        "--root",
        default="docs/llm-wiki",
        help="wiki directory to sweep (default: docs/llm-wiki)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report only; do not append stale markers to INDEX.md",
    )
    args = parser.parse_args(argv)

    root_path = Path(args.root).expanduser()
    if not root_path.is_absolute():
        root_path = (Path.cwd() / root_path).resolve()
    else:
        root_path = root_path.resolve()

    report = _wiki_sweep.sweep(root_path, dry_run=args.dry_run)
    for line in report.summary_lines():
        print(line)
    return 0 if report.is_clean() else 1


def _runs_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="symphony runs",
        description="Print recent run-registry attempts for a WORKFLOW.md.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument("--issue", default=None, help="filter by issue id")
    parser.add_argument("--limit", type=int, default=50, help="max rows, clamped to 1-200")
    args = parser.parse_args(argv)

    workflow_path = resolve_workflow_path(args.workflow)
    registry_path = registry_path_for_workflow(workflow_path)
    if not registry_path.exists():
        print(f"no runs recorded for {workflow_path}")
        return 0

    registry = RunRegistry(registry_path)
    try:
        rows = registry.recent_runs(
            issue_id=args.issue,
            limit=clamp_run_history_limit(args.limit),
        )
    finally:
        registry.close()
    if not rows:
        print(f"no runs recorded for {workflow_path}")
        return 0
    print("identifier  attempt  agent  status  started -> completed")
    for row in rows:
        print(_format_run_row(row))
    return 0


def _format_run_row(row: RunRecord) -> str:
    attempt = row.attempt_kind or "-"
    agent = row.agent_kind or "-"
    started = row.started_at.isoformat() if row.started_at else "-"
    completed = row.completed_at.isoformat() if row.completed_at else "-"
    return (
        f"{row.identifier}  {attempt}  {agent}  {row.status}  "
        f"{started} -> {completed}"
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "board":
        from . import board

        return board.main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "doctor":
        from . import doctor

        return doctor.main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "service":
        from .. import service

        return service.main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "wiki-sweep":
        return _wiki_sweep_main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "runs":
        return _runs_main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "tui":
        # Rewrite `symphony tui [...args]` as `symphony --tui [...args]`.
        raw_argv = ["--tui", *raw_argv[1:]]
    args = _build_parser().parse_args(raw_argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
