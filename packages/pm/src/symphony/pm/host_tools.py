"""Bounded host-tool readiness probes, separate from release-gate readiness."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from symphony.workflow.config import ServiceConfig
from .models import PmError
from .openspec import resolve_node
from .codex_compatibility import REQUIRED_CONTROLS, admit_codex


def probe_codex() -> dict[str, Any]:
    admission = asyncio.run(admit_codex())
    reasons = []
    if admission.reason is not None:
        reasons.append(admission.reason)
    if not (Path.home() / ".codex" / "auth.json").is_file():
        reasons.append(
            "Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login."
        )
    return {
        "required": REQUIRED_CONTROLS, "found": admission.found,
        "available": not reasons, "reason": " ".join(reasons) or None,
    }


def probe_node() -> dict[str, Any]:
    try:
        _, version = resolve_node()
    except PmError as exc:
        match = re.search(r"\(found: (.+)\)$", exc.message)
        found = match[1] if match and match[1] != "none" else None
        return {"required": "20.19.0", "found": found, "available": False, "reason": exc.message}
    return {"required": "20.19.0", "found": version, "available": True, "reason": None}


def probe(cfg: ServiceConfig) -> dict[str, dict[str, Any]]:
    tools = {}
    if cfg.pm.assistance:
        tools["codex"] = probe_codex()
    if cfg.pm.enabled and cfg.pm.openspec_executable is None:
        tools["node"] = probe_node()
    return tools
