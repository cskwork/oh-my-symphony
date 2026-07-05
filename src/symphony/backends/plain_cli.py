"""Shared one-shot plain-stdout CLI backend helpers."""

from __future__ import annotations

import shlex

from . import EVENT_TURN_COMPLETED, BackendInit, TurnResult
from .per_turn import PerTurnCliBackend


class PlainCliBackend(PerTurnCliBackend):
    """One subprocess per turn; treats stdout as the final assistant message."""

    def __init__(
        self,
        init: BackendInit,
        *,
        agent_name: str,
        command: str,
        turn_timeout_ms: int,
        resume_across_turns: bool,
        unattended_flags: tuple[str, ...] = (),
        continuation_flag: str | None = None,
    ) -> None:
        super().__init__(init, agent_name=agent_name, turn_timeout_ms=turn_timeout_ms)
        self._command = command
        self._resume_across_turns = resume_across_turns
        self._unattended_flags = unattended_flags
        self._continuation_flag = continuation_flag

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        del prompt  # travels via stdin
        cmd = self._command
        flags = list(self._unattended_flags)
        if (
            is_continuation
            and self._resume_across_turns
            and self._continuation_flag is not None
        ):
            flags.append(self._continuation_flag)
        for flag in flags:
            if not _has_shell_flag(cmd, flag):
                cmd = f"{cmd} {flag}"
        return cmd

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        await self._emit(
            EVENT_TURN_COMPLETED,
            {
                "result": stdout_text,
                "response": stdout_text,
                "session_id": self._session_id,
                "stats": {},
                "exit_code": rc,
            },
        )
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=self._session_id,
            last_message=stdout_text[:400],
        )


def _has_shell_flag(command: str, *flags: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return any(part in flags for part in parts)
