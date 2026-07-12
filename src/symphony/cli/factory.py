"""`symphony factory` beginner workflow commands."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ..factory.sync import sync_wayfinder
from ..factory.wayfinder import parse_wayfinder_ticket
from ..trackers.file import FileBoardTracker
from ..workflow import SUPPORTED_AGENT_KINDS, build_service_config, load_workflow

_SKILL_SEARCH_ROOTS = (
    Path.home() / ".agents/skills",
    Path.home() / ".codex/skills",
    Path.home() / ".claude/skills",
    Path.home() / ".config/opencode/skills",
)
_BUNDLED_SKILLS_PACKAGE = "symphony.factory.bundled_skills"
_BUNDLED_SKILL_NAMES = {"supergoal", "superdesign", "superpm", "superqa"}
_BACKEND_BLOCKS = {
    "agy": "agy:\n  command: agy --print -\n  resume_across_turns: true\n",
    "claude": (
        "claude:\n"
        "  command: claude -p --output-format stream-json --verbose --permission-mode acceptEdits\n"
    ),
    "codex": (
        "codex:\n"
        "  command: codex app-server\n"
        "  approval_policy: never\n"
        "  thread_sandbox: workspace-write\n"
    ),
    "gemini": "gemini:\n  command: 'gemini -p \"\"'\n  resume_across_turns: true\n",
    "kiro": (
        "kiro:\n"
        "  command: 'kiro-cli chat --no-interactive --trust-all-tools \"$(cat)\"'\n"
        "  resume_across_turns: true\n"
    ),
    "opencode": (
        "opencode:\n"
        "  command: opencode run --format json --auto\n"
        "  resume_across_turns: true\n"
    ),
    "pi": "pi:\n  command: 'pi --mode json -p \"\"'\n",
}
_BACKEND_MARKER = "# __FACTORY_BACKEND_CONFIG__"
WAYFINDER_NEXT_STEP_PROMPT = """Use the supergoal skill in WAYFINDER mode to map my product idea.
Use SuperPM when customer demand, market choice, positioning, or product-spec
evidence is load-bearing; separate observed evidence from assumptions. Write
wayfinder/map.md and vertical slice tickets under wayfinder/tickets/. Do not
write product code.

For every ticket, use this exact parser-compatible Markdown schema:
---
id: stable-ticket-id
title: One vertical slice
route: GREENFIELD
blocked_by: []
skills: []
# kind: ui
# browser: true
---

## Acceptance criteria

- One independently verifiable outcome.

## Proof commands

- `exact command that proves the outcome`

## Non-goals

- Work intentionally excluded from this slice.

Schema rules: route is one of GREENFIELD, DEBUG, LEGACY. blocked_by is a YAML
list of stable ticket ids. skills is a YAML list of path-safe installed skill
names; bundled overlays: superdesign, superpm, superqa. Symphony
adds supergoal automatically.
kind is optional and, when present, is one of
customer-research, research, design, product-spec, qa, ui.
browser is optional and must be the YAML boolean true or false. You may use
## Proof instead of ## Proof commands. Identify the
first unblocked frontier ticket after writing the complete dependency graph."""
_RUNTIME_GITIGNORE_RULES = (
    "/kanban",
    "/.symphony/",
    "/log/",
    "/WORKFLOW-PROGRESS.md",
    "__pycache__/",
    "*.py[cod]",
)


def _copy_assets(target: Path, *, force: bool, agent_kind: str) -> None:
    root = files("symphony.factory.templates")
    conflicts = [path for path in _asset_paths(root) if (target / path).exists()]
    if conflicts and not force:
        raise FileExistsError(f"refusing to overwrite {target / conflicts[0]}; pass --force")
    for relative in _asset_paths(root):
        source = root.joinpath(str(relative))
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if relative == Path("WORKFLOW.md"):
            text = data.decode("utf-8").replace("kind: opencode", f"kind: {agent_kind}")
            text = text.replace(_BACKEND_MARKER, _BACKEND_BLOCKS[agent_kind].rstrip())
            data = text.encode("utf-8")
        destination.write_bytes(data)
    setup = target / "scripts/factory-setup-worktree.sh"
    setup.chmod(setup.stat().st_mode | 0o111)
    (target / "kanban").mkdir(exist_ok=True)


def _merge_runtime_gitignore(target: Path) -> None:
    path = target / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = set(current.splitlines())
    missing = [rule for rule in _RUNTIME_GITIGNORE_RULES if rule not in existing]
    if not missing:
        return
    separator = "" if not current or current.endswith("\n") else "\n"
    heading = "# Oh My Symphony runtime state\n"
    path.write_text(
        current + separator + heading + "\n".join(missing) + "\n",
        encoding="utf-8",
    )


def _asset_paths(root: Any, prefix: Path = Path()) -> list[Path]:
    out: list[Path] = []
    for child in root.iterdir():
        if child.name == "__pycache__":
            continue
        relative = prefix / child.name
        if child.is_dir():
            out.extend(_asset_paths(child, relative))
        elif child.name != "__init__.py":
            out.append(relative)
    return out


def _skill_asset_paths(root: Any, prefix: Path = Path()) -> list[Path]:
    out: list[Path] = []
    for child in root.iterdir():
        if child.name == "__pycache__":
            continue
        relative = prefix / child.name
        if child.is_dir():
            out.extend(_skill_asset_paths(child, relative))
        elif not child.name.endswith(".pyc"):
            out.append(relative)
    return out


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    try:
        skill_sources = _skill_sources({"supergoal"})
        _preflight_init(target, skill_sources, force=args.force)
        _copy_assets(target, force=args.force, agent_kind=args.agent_kind)
        _merge_runtime_gitignore(target)
        _copy_skills(
            target,
            skill_sources,
            force=args.force,
            recovery="rerun 'symphony factory init <project> --force'",
        )
    except (FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"initialized autonomous factory at {target}")
    print(f"Next step: {WAYFINDER_NEXT_STEP_PROMPT}")
    return 0


def _preflight_init(
    target: Path, sources: dict[str, Traversable], *, force: bool
) -> None:
    if force:
        return
    root = files("symphony.factory.templates")
    conflicts = [path for path in _asset_paths(root) if (target / path).exists()]
    if conflicts:
        raise FileExistsError(f"refusing to overwrite {target / conflicts[0]}; pass --force")
    for name in sources:
        destination = target / "skills" / name
        if destination.exists() or destination.is_symlink():
            try:
                _validate_skill_runtime(name, destination)
            except FileNotFoundError as exc:
                raise FileExistsError(
                    f"{destination} is incomplete; rerun "
                    "'symphony factory init <project> --force' to replace it"
                ) from exc


def _sync(
    target: Path,
    wayfinder: Path,
    prefix: str,
    *,
    all_tickets: bool = True,
    force_skills: bool = False,
) -> int:
    requested = {"supergoal"}
    for path in sorted((wayfinder / "tickets").glob("*.md")):
        requested.update(parse_wayfinder_ticket(path).skills)
    _install_skills(target, requested, force=force_skills)
    workflow = target / "WORKFLOW.md"
    cfg = build_service_config(load_workflow(workflow))
    if cfg.tracker.kind != "file":
        raise ValueError("factory requires tracker.kind: file")
    results = sync_wayfinder(
        wayfinder, FileBoardTracker(cfg.tracker), prefix=prefix, all_tickets=all_tickets
    )
    for item in results:
        action = "created" if item.created else "kept"
        print(f"{action} {item.identifier} from {item.key}")
    return 0


def _install_skills(target: Path, names: set[str], *, force: bool) -> None:
    _copy_skills(
        target,
        _skill_sources(names),
        force=force,
        recovery="rerun 'symphony factory sync <project> --force'",
    )


def _skill_sources(names: set[str]) -> dict[str, Traversable]:
    sources: dict[str, Traversable] = {}
    bundled = files(_BUNDLED_SKILLS_PACKAGE)
    for name in sorted(names):
        packaged = bundled.joinpath(name)
        if name in _BUNDLED_SKILL_NAMES and packaged.joinpath("SKILL.md").is_file():
            _validate_skill_runtime(name, packaged)
            sources[name] = packaged
            continue
        source = next(
            (
                root / name
                for root in _SKILL_SEARCH_ROOTS
                if (root / name / "SKILL.md").is_file()
            ),
            None,
        )
        if source is None:
            locations = ", ".join(str(root) for root in _SKILL_SEARCH_ROOTS)
            raise FileNotFoundError(
                f"required custom skill {name!r} is not bundled or installed; "
                f"install it under one of: {locations}"
            )
        source = source.resolve()
        _validate_skill_runtime(name, source)
        sources[name] = source
    return sources


def _validate_skill_runtime(name: str, root: Traversable) -> None:
    required_paths = [Path("SKILL.md")]
    if name in _BUNDLED_SKILL_NAMES:
        packaged = files(_BUNDLED_SKILLS_PACKAGE).joinpath(name)
        required_paths = _skill_asset_paths(packaged)
    for relative in required_paths:
        required = root.joinpath(*Path(relative).parts)
        if not required.is_file():
            raise FileNotFoundError(
                f"required skill {name!r} is incomplete: missing {required}"
            )


def _copy_skills(
    target: Path,
    sources: dict[str, Traversable],
    *,
    force: bool,
    recovery: str,
) -> None:
    for name, source in sources.items():
        destination = target / "skills" / name
        destination_present = destination.exists() or destination.is_symlink()
        if destination_present and not force:
            try:
                _validate_skill_runtime(name, destination)
            except FileNotFoundError as exc:
                raise FileExistsError(
                    f"{destination} is incomplete; {recovery}"
                ) from exc
            continue
        if destination_present:
            _remove_skill_destination(destination)
        if isinstance(source, Path):
            shutil.copytree(source, destination, ignore=_skill_copy_ignore(name, source))
        else:
            _copy_resource_tree(source, destination)


def _remove_skill_destination(destination: Path) -> None:
    if destination.is_symlink() or not destination.is_dir():
        destination.unlink()
    else:
        shutil.rmtree(destination)


def _copy_resource_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in {"__pycache__", ".pytest_cache", "node_modules"}:
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target)
        elif not child.name.endswith(".pyc"):
            target.write_bytes(child.read_bytes())
            if child.name.endswith(".sh"):
                target.chmod(target.stat().st_mode | 0o111)


def _skill_copy_ignore(name: str, root: Path):
    excluded = {".git", ".github", "__pycache__", ".pytest_cache", "node_modules"}
    runtime_roots = {"SKILL.md", "LICENSE", "agents", "reference", "templates"}

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {item for item in names if item in excluded or item.endswith(".pyc")}
        if name == "supergoal" and Path(directory) == root:
            ignored.update(item for item in names if item not in runtime_roots)
        if name == "supergoal" and Path(directory).name == "templates":
            ignored.update({"harness-eval-cases", "harness-eval-external"} & set(names))
        return ignored

    return ignore


def cmd_sync(args: argparse.Namespace) -> int:
    target, wayfinder = _resolve_project_and_wayfinder(args.target, args.wayfinder)
    try:
        return _sync(
            target,
            wayfinder,
            args.prefix,
            all_tickets=args.all_tickets,
            force_skills=args.force,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def run_doctor(workflow: Path, port: int | None = None) -> int:
    from .doctor import main as doctor_main

    argv = [str(workflow)]
    if port is not None:
        argv.extend(["--port", str(port)])
    return doctor_main(argv)


def start_service(argv: list[str]) -> int:
    from ..service import main as service_main

    return service_main(argv)


def cmd_start(args: argparse.Namespace) -> int:
    target, wayfinder = _resolve_project_and_wayfinder(args.target, args.wayfinder)
    workflow = target / "WORKFLOW.md"
    if not workflow.exists():
        init_args = argparse.Namespace(**vars(args))
        init_args.target = str(target)
        init_rc = cmd_init(init_args)
        if init_rc:
            return init_rc
    if not (target / ".git").exists() and not _inside_git_worktree(target):
        print(
            "error: factory start requires a git repository; run `git init` "
            "and commit the starter files first",
            file=sys.stderr,
        )
        return 1
    if wayfinder.exists():
        try:
            _sync(
                target,
                wayfinder,
                args.prefix,
                all_tickets=args.all_tickets,
                force_skills=args.force,
            )
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if run_doctor(workflow, args.port):
        print("error: Doctor failed; service was not started", file=sys.stderr)
        return 1
    argv = ["start", str(workflow), "--skip-doctor"]
    if args.port is not None:
        argv.extend(["--port", str(args.port)])
    return start_service(argv)


def _resolve_project_and_wayfinder(
    positional: str, explicit_wayfinder: str | None
) -> tuple[Path, Path]:
    candidate = Path(positional).resolve()
    if explicit_wayfinder:
        return candidate, Path(explicit_wayfinder).resolve()
    if _looks_like_wayfinder(candidate):
        return candidate.parent, candidate
    return candidate, candidate / "wayfinder"


def _looks_like_wayfinder(path: Path) -> bool:
    if (path / "WORKFLOW.md").is_file():
        return False
    return (
        path.name.lower() == "wayfinder"
        or (path / "map.md").is_file()
        or (path / "tickets").is_dir()
    )


def _inside_git_worktree(target: Path) -> bool:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphony factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="copy the beginner factory bundle")
    init.add_argument("target", nargs="?", default=".")
    init.add_argument("--force", action="store_true")
    init.add_argument(
        "--agent", "--agent-kind", dest="agent_kind",
        choices=sorted(SUPPORTED_AGENT_KINDS), default="opencode"
    )
    init.set_defaults(func=cmd_init)
    sync = sub.add_parser("sync", help="sync Wayfinder tickets to the file board")
    sync.add_argument("target", nargs="?", default=".")
    sync.add_argument("--wayfinder", default=None)
    sync.add_argument("--prefix", default="TASK")
    sync.add_argument(
        "--force",
        action="store_true",
        help="replace generated skill copies from the pinned bundle before syncing",
    )
    sync_scope = sync.add_mutually_exclusive_group()
    sync_scope.add_argument(
        "--frontier-only",
        action="store_false",
        dest="all_tickets",
        help="import only tickets whose dependencies are already Done",
    )
    sync_scope.add_argument(
        "--all",
        action="store_true",
        dest="all_tickets",
        help=argparse.SUPPRESS,
    )
    sync.set_defaults(func=cmd_sync, all_tickets=True)
    start = sub.add_parser("start", help="sync, run Doctor, then start managed service")
    start.add_argument("target", nargs="?", default=".")
    start.add_argument("--wayfinder", default=None)
    start.add_argument("--prefix", default="TASK")
    start_scope = start.add_mutually_exclusive_group()
    start_scope.add_argument(
        "--frontier-only",
        action="store_false",
        dest="all_tickets",
        help="import only tickets whose dependencies are already Done",
    )
    start_scope.add_argument(
        "--all",
        action="store_true",
        dest="all_tickets",
        help=argparse.SUPPRESS,
    )
    start.add_argument("--force", action="store_true")
    start.add_argument(
        "--agent", "--agent-kind", dest="agent_kind",
        choices=sorted(SUPPORTED_AGENT_KINDS), default="opencode"
    )
    start.add_argument("--port", type=int, default=None)
    start.set_defaults(func=cmd_start, all_tickets=True)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
