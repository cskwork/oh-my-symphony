"""SPEC §11.1 — tracker adapter contract and factory.

Every tracker adapter MUST implement these operations and return normalized
`Issue` objects (§4.1.1). Transport details are adapter-defined.

Adapters live as submodules:

    symphony.trackers.file    -> FileBoardTracker  (Markdown ticket files)
    symphony.trackers.linear  -> LinearClient      (Linear GraphQL)
    symphony.trackers.jira    -> JiraClient        (Jira Cloud REST API v3)
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from ..errors import UnsupportedTrackerKind
from ..issue import Issue
from ..workflow import ServiceConfig


@runtime_checkable
class TrackerClient(Protocol):
    def fetch_candidate_issues(self) -> list[Issue]: ...

    def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]: ...

    def fetch_issue_states_by_ids(self, ids: Iterable[str]) -> list[Issue]: ...

    def fetch_issue_full_by_id(self, issue_id: str) -> Issue | None:
        """Return a fully-hydrated `Issue` (with description) by id, or
        `None` when the id is unknown to the adapter.

        Required by the stage-contract validator (§16.5 / v0.6.7+), which
        evaluates ``Issue.description`` for required sections at every
        forward phase transition. The cheap `fetch_issue_states_by_ids`
        path intentionally omits description for poll-hot loops, so a
        separate accessor is needed when the body itself is the signal.
        """
        ...

    def update_state(self, issue: Issue, target_state: str) -> None:
        """Mutate the tracker so `issue` lands in `target_state`.

        Adapters pick whichever identifier field they need (Linear takes
        the UUID `id`, the file tracker takes the human `identifier`).
        Implementations should raise on transport failure so callers
        can decide whether to log-and-continue or propagate.
        """
        ...

    def append_note(self, issue: Issue, heading: str, body: str) -> None:
        """Append a tracker-native note/comment when the backend supports it."""
        ...

    def close(self) -> None: ...


def build_tracker_client(cfg: ServiceConfig) -> TrackerClient:
    """Return the adapter selected by `tracker.kind`."""
    kind = cfg.tracker.kind
    if kind == "linear":
        from .linear import LinearClient

        return LinearClient(cfg.tracker)
    if kind == "file":
        from .file import FileBoardTracker

        return FileBoardTracker(cfg.tracker)
    if kind == "jira":
        from .jira import JiraClient

        return JiraClient(cfg.tracker)
    raise UnsupportedTrackerKind("tracker kind not supported", kind=kind)


def context_manager(cfg: ServiceConfig):
    """Context manager wrapper since not all clients are context managers."""

    class _Wrapper:
        def __enter__(self) -> TrackerClient:
            self._client = build_tracker_client(cfg)
            return self._client

        def __exit__(self, *_args: object) -> None:
            try:
                self._client.close()
            except Exception:
                pass

    return _Wrapper()


__all__ = [
    "TrackerClient",
    "build_tracker_client",
    "context_manager",
]
