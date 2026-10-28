"""Interaction-mode variants of a ServiceConfig (``qa`` = read-only, ``edit``).

Shared by the optional operator chat and the continuous-improvement sweep,
so the orchestrator never imports the chat distribution.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import replace

from .workflow import ServiceConfig

_PERMISSION_MODE_RE = re.compile(r"\s--permission-mode(?:[ =]\S+)?")


def _claude_command_for_mode(
    command: str, mode: str, resume_session_id: str | None = None
) -> str:
    """Strip any configured --permission-mode, then append the chat one.

    Append wins even if a quoted remnant survived the strip: the claude CLI
    keeps the last value of a repeated single-value option. `--resume` is
    injected the same way so a rebuilt backend rejoins the prior session on
    its first (non-continuation) turn; the backend's own resume logic appends
    a later `--resume` on turns 2+, which again wins by position.
    """
    base = _PERMISSION_MODE_RE.sub("", command).strip()
    base += " --permission-mode " + ("plan" if mode == "qa" else "acceptEdits")
    if resume_session_id:
        base += f" --resume {shlex.quote(resume_session_id)}"
    return base


def cfg_for_mode(
    cfg: ServiceConfig,
    mode: str,
    agent_kind: str,
    resume_session_id: str | None = None,
) -> tuple[ServiceConfig, bool]:
    """Derive a mode-specific ServiceConfig variant; returns (cfg, mode_enforced)."""
    if agent_kind != cfg.agent.kind:
        cfg = replace(cfg, agent=replace(cfg.agent, kind=agent_kind))
    if agent_kind == "claude":
        command = _claude_command_for_mode(cfg.claude.command, mode, resume_session_id)
        return replace(cfg, claude=replace(cfg.claude, command=command)), True
    if agent_kind == "codex":
        if mode == "qa":
            codex = replace(
                cfg.codex,
                thread_sandbox="read-only",
                turn_sandbox_policy="read-only",
            )
            return replace(cfg, codex=codex), True
        return cfg, True
    return cfg, False
