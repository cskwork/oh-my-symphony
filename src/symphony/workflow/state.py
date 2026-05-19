"""SPEC §6.2 — last-known-good config holder for hot reloads.

The orchestrator polls `WorkflowState.reload()` to refresh in-process
config without restarting. A broken WORKFLOW.md preserves the prior
`current()` and surfaces the parse/build error via `last_error()` so the
TUI can show a yellow banner without taking the whole service down.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .builder import build_service_config
from .config import ServiceConfig
from .parser import load_workflow


class WorkflowState:
    """Last-known-good config holder for §6.2 reload semantics."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._config: ServiceConfig | None = None
        self._last_error: Exception | None = None
        self._lock = threading.RLock()

    def reload(self) -> tuple[ServiceConfig | None, Exception | None]:
        try:
            wf = load_workflow(self.path)
            cfg = build_service_config(wf)
        except Exception as exc:
            with self._lock:
                self._last_error = exc
            return None, exc
        with self._lock:
            self._config = cfg
            self._last_error = None
        return cfg, None

    def current(self) -> ServiceConfig | None:
        with self._lock:
            return self._config

    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error
