"""Manage independent Symphony projects through a central registry."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .. import service
from ..projects import (
    Project,
    ProjectError,
    ProjectRegistry,
    create_or_adopt_project,
    source_checkout,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999


def cmd_list(args: argparse.Namespace) -> int:
    projects = ProjectRegistry().load()
    if not projects:
        print("(no projects)")
        return 0
    print("ID  NAME  ENDPOINT  REPOSITORY  WORKFLOW")
    for project in projects:
        print(
            f"{project.id}  {project.name}  {project.host}:{project.port}  "
            f"{project.git_repo}  {project.workflow}"
        )
    return 0


def _setup_from_args(args: argparse.Namespace, target: Path, *, name: str) -> Project:
    """Keep CLI argument adaptation separate from the shared domain service."""
    return create_or_adopt_project(
        target,
        source=source_checkout(),
        name=name,
        project_id=args.id,
        workflow=args.workflow,
        host=args.host,
        port=args.port,
        registry=ProjectRegistry(),
        with_skill=args.with_skill,
    )


def cmd_add(args: argparse.Namespace) -> int:
    target = Path(args.repo).expanduser().resolve()
    project = _setup_from_args(args, target, name=args.name or target.name)
    print(f"added project {project.id} ({project.git_repo}) at {project.host}:{project.port}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    source = source_checkout()
    project_id = args.id
    default_id = project_id or re.sub(
        r"[^a-z0-9]+", "-", args.name.strip().lower()
    ).strip("-")
    if not default_id:
        raise ProjectError("name must contain at least one letter or digit")
    default_parent = source.parent if source is not None else Path.cwd()
    target = (
        Path(args.path).expanduser().resolve()
        if args.path
        else (default_parent / default_id[:64].rstrip("-")).resolve()
    )
    # Avoid locating the source checkout twice while retaining the small common
    # CLI adapter used by add.
    project = create_or_adopt_project(
        target,
        source=source,
        name=args.name,
        project_id=project_id,
        workflow=args.workflow,
        host=args.host,
        port=args.port,
        registry=ProjectRegistry(),
        with_skill=args.with_skill,
    )
    print(f"created project {project.id} at {project.git_repo} ({project.host}:{project.port})")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    project = ProjectRegistry().remove(args.id)
    print(f"removed project {project.id}; repository left untouched at {project.git_repo}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    project = ProjectRegistry().get(args.id)
    argv = [
        "start",
        project.workflow,
        "--host",
        project.host,
        "--port",
        str(project.port),
    ]
    if args.replace:
        argv.append("--replace")
    if args.skip_doctor:
        argv.append("--skip-doctor")
    return service.main(argv)


def cmd_stop(args: argparse.Namespace) -> int:
    project = ProjectRegistry().get(args.id)
    argv = ["stop", project.workflow, "--timeout", str(args.timeout)]
    if args.force:
        argv.append("--force")
    return service.main(argv)


def _status(project: Project) -> int:
    return service.main(["status", project.workflow, "--port", str(project.port)])


def cmd_status(args: argparse.Namespace) -> int:
    registry = ProjectRegistry()
    if args.id is not None:
        return _status(registry.get(args.id))
    projects = registry.load()
    if not projects:
        print("(no projects)")
        return 0
    result = 0
    for project in projects:
        print(f"[{project.id}] {project.name}")
        result = max(result, _status(project))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony project",
        description="Register and run independent Symphony project services.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", aliases=["ls"], help="list registered projects").set_defaults(func=cmd_list)

    def add_registration_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--with-skill", action="store_true",
            help="copy the packaged operator skills and Claude discovery link",
        )
        command.add_argument("--id", default=None, help="stable lowercase project id")
        command.add_argument("--workflow", default="WORKFLOW.md", help="workflow path relative to repository")
        command.add_argument("--host", default=DEFAULT_HOST)
        command.add_argument("--port", type=int, default=None, help="service port (default: next unused from 9999)")

    add = sub.add_parser("add", help="register an existing git repository")
    add.add_argument("repo", help="path inside the existing repository")
    add.add_argument("--name", default=None)
    add_registration_options(add)
    add.set_defaults(func=cmd_add)

    create = sub.add_parser("create", help="create and bootstrap a git repository")
    create.add_argument("name", help="display name")
    create.add_argument("--path", default=None, help="target path (default: sibling of editable source, otherwise current directory)")
    add_registration_options(create)
    create.set_defaults(func=cmd_create)

    remove = sub.add_parser("remove", aliases=["rm"], help="unregister without deleting files")
    remove.add_argument("id")
    remove.set_defaults(func=cmd_remove)

    start = sub.add_parser("start", help="start a project's managed service")
    start.add_argument("id")
    start.add_argument("--replace", action="store_true")
    start.add_argument("--skip-doctor", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="stop a project's managed service")
    stop.add_argument("id")
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--force", action="store_true")
    stop.set_defaults(func=cmd_stop)

    status = sub.add_parser("status", help="show one or every project's service status")
    status.add_argument("id", nargs="?", default=None)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
