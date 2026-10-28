"""Portable project resources, shared by config building, bootstrap and eject."""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from functools import cache
from importlib.resources import as_file, files
from pathlib import Path
import shlex
from typing import Any

import yaml

_resources = ExitStack()
atexit.register(_resources.close)


@cache
def resource_path(relative: str) -> Path:
    """Keep extracted resources alive for the lifetime of the orchestrator."""
    resource = files(__package__).joinpath(relative)
    return _resources.enter_context(as_file(resource)).resolve()


def standard_defaults(*, local: bool = False) -> dict[str, Any]:
    """Load fresh mutable defaults; explicit workflow values are merged later."""
    raw = yaml.safe_load(resource_path("defaults.yaml").read_text(encoding="utf-8"))
    prompts = raw["prompts"]
    if not local:
        prompts["base"] = str(resource_path(prompts["base"].removeprefix("./")))
        prompts["stages"] = {
            state: str(resource_path(path.removeprefix("./")))
            for state, path in prompts["stages"].items()
        }
        script = resource_path("scripts/symphony-setup-worktree.sh")
        raw["hooks"]["after_create"] = f"bash {shlex.quote(str(script))}\n"
    return raw


def fill_missing(explicit: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Merge absent keys only, including partial hook and prompt maps."""
    result = dict(defaults)
    for key, value in explicit.items():
        if (
            key not in {"stages", "state_descriptions"}
            and isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = fill_missing(value, result[key])
        else:
            result[key] = value
    return result
