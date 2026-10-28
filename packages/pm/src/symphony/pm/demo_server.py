"""Serve a disposable PM demo through the normal authenticated web app."""

from __future__ import annotations

import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.webapi import BIND_HOST_KEY
from .demo import DemoError, check_demo_paths
from .routes import PM_DEMO_KEY, PM_DEVELOPMENT_VERIFICATION_KEY


@web.middleware
async def demo_read_only(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if request.path == "/api/v1/chat/ws" or (
        request.path.startswith("/api/")
        and not request.path.startswith("/api/v1/pm/")
        and request.method not in {"GET", "HEAD"}
    ):
        return web.json_response(
            {"error": {
                "code": "demo_read_only",
                "message": "Only PM Workspace actions are available in the demo",
            }},
            status=403,
        )
    return await handler(request)


def build_demo_app(orchestrator: Orchestrator, demo_dir: Path) -> web.Application:
    """Require isolated caller setup before enabling demo-only mutations."""
    directory = demo_dir.resolve()
    projects_file = os.environ.get("SYMPHONY_PROJECTS_FILE", "")
    if not projects_file or Path(projects_file).expanduser().resolve() != directory / "projects.json":
        raise DemoError("demo server requires SYMPHONY_PROJECTS_FILE=DIR/projects.json")
    if os.environ.get("SYMPHONY_TRUSTED_ORIGINS", ""):
        raise DemoError("demo server requires empty SYMPHONY_TRUSTED_ORIGINS")
    check_demo_paths(orchestrator.workflow_state.current(), directory)
    app = build_app(orchestrator)
    app[PM_DEVELOPMENT_VERIFICATION_KEY] = True
    app[PM_DEMO_KEY] = directory
    app[BIND_HOST_KEY] = "127.0.0.1"
    # build_app installs _api_guard first: authentication, Host and Origin
    # checks must run before this demo restriction.
    app.middlewares.append(demo_read_only)

    async def cleanup(_app: web.Application) -> None:
        await orchestrator.stop()

    app.on_cleanup.append(cleanup)
    return app


def bind_demo_socket() -> socket.socket:
    """Reserve the OS-selected port before the CLI claims a demo directory."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(socket.SOMAXCONN)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


async def start_demo_server(
    app: web.Application, sock: socket.socket
) -> tuple[web.AppRunner, int]:
    """Serve the already-bound socket; the caller cleans up the returned runner."""
    runner = web.AppRunner(app)
    try:
        port = sock.getsockname()[1]
        await runner.setup()
        await web.SockSite(runner, sock).start()
    except BaseException:
        try:
            await runner.cleanup()
        finally:
            sock.close()
        raise
    return runner, port
