"""Prime Agent CLI backend.

Drives `prime-agent -p --mode json` once per turn. Prime Agent
(https://github.com/cskwork/prime-agent) auto-saves sessions under
``~/.prime/agent/sessions/``; multi-turn continuity uses
``--resume <id>``.

The JSON protocol is identical to Pi's — same NDJSON event stream
(``session``, ``agent_start``, ``turn_start``, ``message_start``,
``message_update``, ``message_end``, ``turn_end``, ``agent_end``).
This module is a thin subclass that swaps the config source and resume
flag.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..issue import normalize_state, workspace_key
from ..trackers.file import issue_from_file
from ..workflow import PiConfig, PrimeAgentConfig
from . import BackendInit
from .pi import PiBackend


class PrimeAgentBackend(PiBackend):
    """One subprocess per turn; speaks prime-agent --mode json JSONL."""

    _agent_name = "prime-agent"
    _resume_flag = "--resume"

    def __init__(self, init: BackendInit) -> None:
        # PiBackend reads its command and turn timeout through
        # ``_turn_config``, which points at ``prime_agent`` here.
        super().__init__(init)
        self._pi_tracker = init.cfg.tracker

    @staticmethod
    def _turn_config(init: BackendInit) -> PiConfig | PrimeAgentConfig:
        return init.cfg.prime_agent

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "prime-agent"}

    async def _consume_stream(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Accept a clean missing trailer only after the file ticket is terminal.

        Prime Agent can exit successfully after its final board-update tool call
        without flushing ``agent_end``.  Returning a synthetic terminal event
        here lets the inherited return-code check decide success: rc=0 completes,
        while a non-zero exit still fails.  An empty response for an active (or
        unidentifiable) ticket remains a normal missing-``agent_end`` failure.
        """
        terminal = await super()._consume_stream(proc)
        if terminal is not None or not self._file_ticket_is_terminal():
            return terminal
        return {
            "type": "agent_end",
            "messages": [],
            "synthetic_reason": "ticket_terminal_without_agent_end",
        }

    def _file_ticket_is_terminal(self) -> bool:
        tracker = self._pi_tracker
        if tracker.kind != "file" or tracker.board_root is None:
            return False

        workspace_name = self._cwd.name
        terminal_states = {normalize_state(state) for state in tracker.terminal_states}
        matching_states: list[str] = []
        try:
            for path in tracker.board_root.glob("*.md"):
                issue = issue_from_file(path)
                if (
                    issue is not None
                    and workspace_key(issue.identifier) == workspace_name
                ):
                    matching_states.append(normalize_state(issue.state))
        except Exception:
            # A malformed or concurrently-written board must fail closed: it
            # must never turn a missing terminal event into a false success.
            return False
        # Sanitization can map distinct identifiers to the same workspace key.
        # Fail closed rather than accepting an ambiguous ticket match.
        return len(matching_states) == 1 and matching_states[0] in terminal_states
