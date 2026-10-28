"""Stable host project identity shared by PM HTTP and native dispatch."""

from __future__ import annotations

import hashlib
from pathlib import Path

from symphony.projects import ProjectRegistry
from .models import PmError


def host_project_identity(workflow_path: Path, *, standalone_id: str | None = None) -> str:
    """Return the registered project ID, or the standalone ID when unregistered.

    An absent host registry means standalone operation. A registry that exists
    but cannot be read raises instead of guessing: falling back to the tracker
    slug would look up PM authority under the wrong key and could treat
    managed work as ordinary work.
    """
    workflow = workflow_path.expanduser().resolve()
    try:
        current = next(
            (project for project in ProjectRegistry().list()
             if Path(project.workflow).expanduser().resolve() == workflow),
            None,
        )
    except Exception as exc:
        raise PmError(
            "project_identity_unavailable",
            f"host project registry is unreadable: {exc}",
            status=503,
        ) from exc
    if current is not None:
        return current.id
    if standalone_id and standalone_id.strip():
        return standalone_id.strip()
    digest = hashlib.sha256(str(workflow).encode()).hexdigest()[:24]
    return f"standalone-{digest}"
