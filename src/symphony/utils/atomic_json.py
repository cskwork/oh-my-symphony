"""Atomic JSON state files for orchestrator-owned ``.symphony/`` records.

``os.replace`` is atomic on POSIX and on NTFS, but Windows refuses the rename
with ``PermissionError`` (``WinError 32``) while another process holds the
target open, for example a TUI or an HTTP poll reading the file. Retrying a
couple of times with a tiny backoff clears that common case without blocking
dispatch; the caller still treats a final failure as best-effort.

State files are namespaced per workflow file so two orchestrators whose
``WORKFLOW*.md`` files share one directory do not overwrite each other's
records (GitHub issue #32).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_RETRY_BACKOFF_S: tuple[float, ...] = (0.01, 0.05)
_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_WORKFLOW_NAME = "WORKFLOW.md"


def state_file_name(workflow_path: Path, base: str) -> str:
    """Return the ``.symphony/`` file name for one state record.

    The canonical ``WORKFLOW.md`` keeps the historical ``<base>.json`` name so
    existing deployments load their records unchanged. Any other workflow
    file name is folded into the record name, so sibling workflows in one
    directory own separate files.
    """
    name = workflow_path.name
    if name == _DEFAULT_WORKFLOW_NAME:
        return f"{base}.json"
    stem = name[:-3] if name.lower().endswith(".md") else name
    stem = _STEM_RE.sub("-", stem).strip("-.") or "workflow"
    return f"{base}.{stem}.json"


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    attempts: int = 3,
    backoff_s: tuple[float, ...] = _RETRY_BACKOFF_S,
) -> None:
    """Write ``payload`` as JSON to ``path`` via a temp file and rename.

    Raises ``OSError`` (including ``PermissionError``) when the rename still
    fails after ``attempts`` tries; the temp file is removed in every failure
    path so a busy directory does not accumulate residue.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, indent=2))
        total = max(1, attempts)
        for attempt in range(total):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt + 1 >= total:
                    raise
                time.sleep(backoff_s[min(attempt, len(backoff_s) - 1)])
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
