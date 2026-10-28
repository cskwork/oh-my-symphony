"""Materialize the portable defaults for project-local customization."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from ..errors import SymphonyError
from ..templates.project import resource_path, standard_defaults
from ..workflow import build_service_config, load_workflow, resolve_workflow_path
from ..workflow.mutate import (
    WorkflowMutationError,
    _load_frontmatter,
    _write_workflow_atomic,
)


def _fill_missing(target: CommentedMap, defaults: dict[str, Any]) -> None:
    for key, value in defaults.items():
        if key in target:
            if (
                key not in {"stages", "state_descriptions"}
                and isinstance(target[key], CommentedMap)
                and isinstance(value, dict)
            ):
                _fill_missing(target[key], value)
            continue
        if isinstance(value, dict):
            nested = CommentedMap()
            _fill_missing(nested, value)
            target[key] = nested
        else:
            target[key] = (
                LiteralScalarString(value)
                if isinstance(value, str) and "\n" in value
                else value
            )


def eject(workflow: Path, selection: str = "all") -> list[Path]:
    """Preflight every destination; never replace even a dangling symlink."""
    if selection not in {"all", "prompts", "hooks"}:
        raise WorkflowMutationError(f"unknown eject selection: {selection}")
    build_service_config(load_workflow(workflow), log_decisions=False)
    data, body = _load_frontmatter(workflow)
    defaults = standard_defaults(local=True)
    relatives: list[str] = []
    selected: dict[str, Any] = {}
    if selection in {"all", "prompts"}:
        prompts = defaults["prompts"]
        relatives.extend([prompts["base"], *prompts["stages"].values()])
        selected["prompts"] = prompts
    if selection in {"all", "hooks"}:
        relatives.append("scripts/symphony-setup-worktree.sh")
        selected["hooks"] = defaults["hooks"]
    targets = [
        (resource_path(relative.removeprefix("./")), workflow.parent / relative)
        for relative in dict.fromkeys(relatives)
    ]
    for _, destination in targets:
        if destination.exists() or destination.is_symlink():
            raise WorkflowMutationError(
                f"refusing to overwrite existing file: {destination}"
            )
        parent = destination.parent
        while parent != workflow.parent:
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise WorkflowMutationError(
                    f"eject path requires a directory: {parent}"
                )
            parent = parent.parent
    _fill_missing(data, selected)
    written: list[Path] = []
    directories: list[Path] = []
    try:
        for source, destination in targets:
            missing: list[Path] = []
            parent = destination.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                directories.append(parent)
            with destination.open("xb") as output:
                written.append(destination)
                output.write(source.read_bytes())
            if destination.suffix == ".sh":
                destination.chmod(destination.stat().st_mode | 0o111)
        _write_workflow_atomic(workflow, data, body)
    except Exception:
        for destination in reversed(written):
            destination.unlink()
        for directory in reversed(directories):
            directory.rmdir()
        raise
    return [*written, workflow]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="symphony eject", description=__doc__)
    parser.add_argument(
        "selection", choices=("prompts", "hooks", "all"), nargs="?", default="all"
    )
    parser.add_argument("workflow", nargs="?", default=None)
    raw_args = list(argv if argv is not None else sys.argv[1:])
    if (
        raw_args
        and not raw_args[0].startswith("-")
        and raw_args[0] not in {"prompts", "hooks", "all"}
    ):
        raw_args.insert(0, "all")
    args = parser.parse_args(raw_args)
    try:
        for path in eject(resolve_workflow_path(args.workflow), args.selection):
            print(f"wrote {path}")
        return 0
    except (SymphonyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
