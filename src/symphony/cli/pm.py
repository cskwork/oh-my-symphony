"""Create and serve a disposable PM Workspace demo without dispatching agents."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from typing import Any

from .. import PM_INSTALL_HINT, pm_installed
from ..orchestrator import Orchestrator
from ..workflow import WorkflowState

# The PM Workspace is an optional distribution; without it `main` only prints
# the install hint. The names stay module attributes for test monkeypatching.
if pm_installed():
    from ..pm.demo import (  # pyright: ignore[reportMissingImports]
        DemoError,
        DemoWorkspace,
        claim_demo_directory,
        create_demo,
        preflight_demo,
    )
    from ..pm.demo_server import bind_demo_socket, build_demo_app, start_demo_server  # pyright: ignore[reportMissingImports]


async def _serve(workspace: DemoWorkspace, sock: socket.socket, trusted_origins: bool) -> None:
    state = WorkflowState(workspace.workflow_path)
    config, error = state.reload()
    if config is None:
        raise DemoError(f"demo workflow could not be loaded: {error}")
    orchestrator = Orchestrator(state)
    runner = None
    try:
        app = build_demo_app(orchestrator, workspace.directory)
        runner, port = await start_demo_server(app, sock)
        print(f"PM Workspace demo ({workspace.lang}) ready")
        print(f"Folder: {workspace.directory}")
        print(f"Open: http://127.0.0.1:{port}/?lang={workspace.lang}#/artifacts")
        print("Agents never start in the demo. Press Ctrl-C to stop.")
        if os.environ.get("SYMPHONY_API_TOKEN", "").strip():
            print("SYMPHONY_API_TOKEN is set: the page asks for it once per browser tab.")
        if trusted_origins:
            print("SYMPHONY_TRUSTED_ORIGINS is ignored: the demo accepts only local addresses.")
        sys.stdout.flush()
        await asyncio.Future()
    finally:
        if runner is not None:
            await runner.cleanup()
        else:
            await orchestrator.stop()


def _failure(exc: Exception) -> int:
    print(str(exc), file=sys.stderr)
    if isinstance(exc, DemoError):
        demo_error: Any = exc  # Pyright cannot resolve the optional PM package.
        for finding in demo_error.findings:
            print(f"{finding.code}: {finding.message}", file=sys.stderr)
        return demo_error.exit_code
    return 1


def main(argv: list[str] | None = None) -> int:
    if not pm_installed():
        print(f"symphony pm: {PM_INSTALL_HINT}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="symphony pm")
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="create and serve a disposable PM Workspace demo")
    demo.add_argument("--path", metavar="DIR")
    demo.add_argument("--lang", metavar="en|ko")
    args = parser.parse_args(argv)
    sock = None
    directory = None
    try:
        try:
            preflight = preflight_demo(args.path, args.lang)
            sock = bind_demo_socket()
            directory = claim_demo_directory(preflight)
            workspace = create_demo(directory, preflight.lang)
        except KeyboardInterrupt:
            message = (
                f"Interrupted; the partial demo is kept at {directory}"
                if directory is not None else "Interrupted; no demo folder was claimed"
            )
            print(message, file=sys.stderr)
            return 130
        except Exception as exc:
            return _failure(exc)

        trusted_origins = bool(os.environ.get("SYMPHONY_TRUSTED_ORIGINS", ""))
        # Both values are read per request: isolate registry lookups and narrow
        # Origin/Host access throughout serving, while keeping the API token.
        os.environ["SYMPHONY_PROJECTS_FILE"] = str(directory / "projects.json")
        os.environ.pop("SYMPHONY_TRUSTED_ORIGINS", None)
        try:
            asyncio.run(_serve(workspace, sock, trusted_origins))
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            return _failure(exc)
        return 0
    finally:
        if sock is not None:
            sock.close()
