"""Antigravity CLI backend (`agy`)."""

from __future__ import annotations

from . import BackendInit
from .plain_cli import PlainCliBackend


class AgyBackend(PlainCliBackend):
    """Drive `agy --print -` once per Symphony worker turn."""

    def __init__(self, init: BackendInit) -> None:
        cfg = init.cfg.agy
        super().__init__(
            init,
            agent_name="agy",
            command=cfg.command,
            turn_timeout_ms=cfg.turn_timeout_ms,
            resume_across_turns=cfg.resume_across_turns,
            unattended_flags=("--dangerously-skip-permissions",),
            continuation_flag="--continue",
        )
