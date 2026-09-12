"""Board dependency-graph validation shared by the board CLI and the web API.

Dependency-light on purpose: only the Issue model and typed errors, so both
`symphony.cli.board` and `symphony.webapi` apply identical rules when a
ticket is created or updated with `blocked_by` edges.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from ..errors import BoardDependencyError
from ..issue import Issue

IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
IDENTIFIER_RULE = "^[A-Za-z][A-Za-z0-9_-]{0,63}$"


def validate_identifier(raw: str, *, field: str = "identifier") -> str:
    """Whitelist a ticket identifier before it ever touches the filesystem.

    Every board write builds `board_root / f"{identifier}.md"`, so an
    identifier carrying `/`, `\\` or `..` escapes the board root. The CLI,
    the web API and `FileBoardTracker` all funnel through this one gate so
    they cannot drift apart (defence in depth).
    """
    identifier = (raw or "").strip()
    if not IDENTIFIER_RE.match(identifier):
        raise BoardDependencyError(
            f"{field} must match {IDENTIFIER_RULE}",
            identifier=identifier or "<empty>",
        )
    return identifier


def blocker_ids(issue: Issue) -> list[str]:
    """Blocker identifiers for one issue, in frontmatter order."""
    out: list[str] = []
    for blocker in issue.blocked_by:
        ident = blocker.identifier or blocker.id
        if ident and ident not in out:
            out.append(ident)
    return out


def board_edges(issues: Sequence[Issue]) -> dict[str, tuple[str, ...]]:
    """``identifier -> blocker identifiers`` for every board ticket."""
    return {issue.identifier: tuple(blocker_ids(issue)) for issue in issues}


def dangling_blockers(issues: Sequence[Issue]) -> dict[str, list[str]]:
    """``identifier -> blocker ids that do not exist on the board``."""
    known = {issue.identifier for issue in issues}
    out: dict[str, list[str]] = {}
    for issue in issues:
        missing = [b for b in blocker_ids(issue) if b not in known]
        if missing:
            out[issue.identifier] = missing
    return out


def find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """Return one dependency cycle as ``[a, b, ..., a]``, or None.

    Dangling targets (not present as keys) cannot close a cycle and are
    skipped.
    """
    visiting: set[str] = set()
    done: set[str] = set()
    path: list[str] = []

    for node in sorted(edges):
        if node in done:
            continue
        visiting.add(node)
        path.append(node)
        stack = [iter(edges[node])]
        while stack:
            dep = next(stack[-1], None)
            if dep is None:
                stack.pop()
                done.add(path[-1])
                visiting.remove(path.pop())
                continue
            if dep not in edges:
                continue
            if dep in visiting:
                return path[path.index(dep) :] + [dep]
            if dep not in done:
                visiting.add(dep)
                path.append(dep)
                stack.append(iter(edges[dep]))
    return None


def _find_cycle_through(
    edges: Mapping[str, Sequence[str]], node: str
) -> list[str] | None:
    """Return a cycle passing through ``node``, or None.

    Only cycles that involve the edited ticket are reported, so a
    pre-existing cycle elsewhere on a hand-edited board never blocks
    unrelated ticket writes.
    """
    path = [node]
    visited: set[str] = set()

    stack = [iter(edges.get(node, ()))]
    while stack:
        dep = next(stack[-1], None)
        if dep is None:
            stack.pop()
            path.pop()
            continue
        if dep == node:
            return [*path, node]
        if dep in visited or dep not in edges:
            continue
        visited.add(dep)
        path.append(dep)
        stack.append(iter(edges[dep]))
    return None


def topological_order(edges: Mapping[str, Sequence[str]]) -> list[str]:
    """Blockers-first order (ties broken by identifier). Caller must have
    rejected cycles via :func:`find_cycle` first; cyclic leftovers are
    appended in identifier order so output never silently drops tickets."""
    remaining = {node: {d for d in deps if d in edges} for node, deps in edges.items()}
    dependents: dict[str, list[str]] = {node: [] for node in edges}
    for node, deps in remaining.items():
        for dep in deps:
            dependents[dep].append(node)
    ready = sorted(node for node, deps in remaining.items() if not deps)
    order: list[str] = []
    while ready:
        next_ready: list[str] = []
        for node in ready:
            order.append(node)
            del remaining[node]
            for dependent in dependents[node]:
                deps = remaining[dependent]
                deps.remove(node)
                if not deps:
                    next_ready.append(dependent)
        # Preserve sorted waves, rather than letting a newly-ready ticket
        # jump ahead of the rest of the current wave.
        ready = sorted(next_ready)
    order.extend(sorted(remaining))
    return order


def validate_ticket_dependencies(
    issues: Sequence[Issue],
    *,
    identifier: str | None,
    blocked_by: Sequence[str],
    new_ticket: bool,
) -> None:
    """Reject a ticket write that would break the board dependency DAG.

    Rules (identical for CLI and web API):
      * a new ticket id must be unique on the board,
      * every ``blocked_by`` target must already exist on the board,
      * the edges must keep the graph acyclic (checked through the edited
        ticket, so pre-existing unrelated cycles do not block the write).

    ``identifier=None`` means "id will be freshly generated": nothing can
    reference it yet, so only blocker existence is checked.
    """
    if identifier is not None:
        identifier = validate_identifier(identifier)
    for target in blocked_by:
        validate_identifier(target, field="blocked_by target")
    known = {issue.identifier for issue in issues}
    if new_ticket and identifier is not None and identifier in known:
        raise BoardDependencyError(
            "ticket already exists", identifier=identifier
        )
    missing = sorted(set(blocked_by) - known)
    if missing:
        raise BoardDependencyError(
            f"unknown blocked_by target(s): {', '.join(missing)}",
            identifier=identifier or "<new>",
        )
    if identifier is None or not blocked_by:
        return
    edges = board_edges(issues)
    edges[identifier] = tuple(blocked_by)
    cycle = _find_cycle_through(edges, identifier)
    if cycle is not None:
        raise BoardDependencyError(
            f"blocked_by would create a dependency cycle: {' -> '.join(cycle)}",
        )
