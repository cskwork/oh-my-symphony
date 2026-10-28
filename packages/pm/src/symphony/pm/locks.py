"""Cross-process per-change authority lock shared by PM mutations and launch."""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from symphony.orchestrator.run_registry import RunRegistry


_HELD_AUTHORITY_LOCKS = threading.local()


@contextmanager
def change_authority_lock(
    registry: RunRegistry, project_id: str, change_id: str, *, shared: bool = False
):
    root = registry.path.parent / "pm-locks"
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{project_id}:{change_id}".encode()).hexdigest()
    key = (root / f"{name}.lock").resolve()
    held: dict[Path, bool] = _HELD_AUTHORITY_LOCKS.__dict__.setdefault("modes", {})
    if key in held:
        if held[key] and not shared:
            raise RuntimeError("a shared authority lock cannot be upgraded in place")
        yield
        return
    handle = key.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        held[key] = shared
        yield
    finally:
        held.pop(key, None)
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
