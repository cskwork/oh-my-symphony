"""Kiro CLI backend."""

from __future__ import annotations

from . import BackendInit
from .per_turn import _has_shell_flag
from .plain_cli import PlainCliBackend


class KiroBackend(PlainCliBackend):
    """Drive `kiro-cli chat --no-interactive` once per Symphony worker turn."""

    def __init__(self, init: BackendInit) -> None:
        cfg = init.cfg.kiro
        super().__init__(
            init,
            agent_name="kiro",
            command=cfg.command,
            turn_timeout_ms=cfg.turn_timeout_ms,
            resume_across_turns=cfg.resume_across_turns,
            continuation_flag="--resume",
        )

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        """Keep Kiro options before the positional chat input.

        `kiro-cli chat` accepts the rendered prompt as `[INPUT]`. The default
        command uses `"$(cat)"` as that positional argument, so continuation
        flags must be inserted before it instead of appended after it.
        """
        del prompt  # travels via stdin ($(cat) in the command)
        command = self._command
        if not (
            is_continuation
            and self._resume_across_turns
            and not _has_shell_flag(command, "--resume", "-r")
        ):
            return command
        return _insert_before_prompt_arg(command, "--resume")


def _insert_before_prompt_arg(command: str, flag: str) -> str:
    stripped = command.rstrip()
    for marker in ('"$(cat)"', "$(cat)"):
        if stripped.endswith(marker):
            prefix = stripped[: -len(marker)].rstrip()
            return f"{prefix} {flag} {marker}"
    return f"{stripped} {flag}"
