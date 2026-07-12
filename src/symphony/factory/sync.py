"""Idempotently synchronize a validated Wayfinder graph to a file board."""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..trackers.file import FileBoardTracker, parse_ticket_file
from .wayfinder import WayfinderTicket, parse_wayfinder_ticket

_SOURCE_RE = re.compile(
    r"^<!-- symphony-factory-source: id=([A-Za-z0-9][A-Za-z0-9._-]*) path=(.+?) "
    r"sha256=([a-f0-9]{64})(?: managed_sha256=([a-f0-9]{64}))? -->$",
    re.MULTILINE,
)
_END_MARKER = "<!-- /symphony-factory-managed -->"
_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class SyncResult:
    key: str
    identifier: str
    path: Path
    created: bool


def sync_wayfinder(
    wayfinder_root: Path,
    tracker: FileBoardTracker,
    *,
    prefix: str = "TASK",
    all_tickets: bool = True,
) -> list[SyncResult]:
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(
            "ticket prefix must use only letters, numbers, dots, underscores, or hyphens"
        )
    tickets = _load_graph(wayfinder_root)
    order = _topological_order(tickets)
    existing = _existing_sources(tracker)
    identifiers = {key: value[0] for key, value in existing.items()}
    results: list[SyncResult] = []
    created_paths: list[Path] = []
    refreshed_files: dict[Path, bytes] = {}
    issues = {key: tracker.fetch_issue_full_by_id(identifier) for key, identifier in identifiers.items()}
    states = {key: issue.state.lower() if issue else "" for key, issue in issues.items()}
    selected = [
        ticket for ticket in order
        if ticket.key in existing or all_tickets or all(states.get(dep) == "done" for dep in ticket.blocked_by)
    ]
    try:
        for ticket in selected:
            if ticket.key in existing:
                identifier = existing[ticket.key][0]
                path = tracker.find_path(identifier)
                if path is None:
                    raise ValueError(f"provenance points to missing ticket {identifier}")
                refreshed_files[path] = path.read_bytes()
                _refresh_ready_ticket(tracker, identifier, ticket, identifiers, wayfinder_root)
                results.append(SyncResult(ticket.key, identifier, path, False))
                continue
            description = _managed_body(ticket, identifiers, wayfinder_root)
            dependencies = _dependency_identifiers(ticket, identifiers)
            identifier, path = tracker.create_with_next_identifier(
                prefix,
                title=ticket.title,
                state="Ready",
                labels=["factory", f"route:{ticket.route.lower()}"],
                description=description,
                skills=list(ticket.skills),
                blocked_by=dependencies,
            )
            identifiers[ticket.key] = identifier
            created_paths.append(path)
            results.append(SyncResult(ticket.key, identifier, path, True))
    except Exception:
        for path, content in refreshed_files.items():
            path.write_bytes(content)
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        raise
    return results


def _load_graph(root: Path) -> dict[str, WayfinderTicket]:
    ticket_dir = root / "tickets"
    if not ticket_dir.is_dir():
        raise ValueError(f"Wayfinder tickets directory not found: {ticket_dir}")
    tickets: dict[str, WayfinderTicket] = {}
    for path in sorted(ticket_dir.glob("*.md")):
        ticket = parse_wayfinder_ticket(path)
        if ticket.key in tickets:
            raise ValueError(f"duplicate Wayfinder id: {ticket.key}")
        tickets[ticket.key] = ticket
    if not tickets:
        raise ValueError(f"no Wayfinder tickets found under {ticket_dir}")
    missing = sorted(
        {dep for ticket in tickets.values() for dep in ticket.blocked_by if dep not in tickets}
    )
    if missing:
        raise ValueError(f"unknown Wayfinder dependencies: {', '.join(missing)}")
    return tickets


def _topological_order(tickets: dict[str, WayfinderTicket]) -> list[WayfinderTicket]:
    pending = set(tickets)
    resolved: set[str] = set()
    ordered: list[WayfinderTicket] = []
    while pending:
        ready = sorted(key for key in pending if set(tickets[key].blocked_by) <= resolved)
        if not ready:
            raise ValueError(f"cyclic Wayfinder dependencies: {', '.join(sorted(pending))}")
        for key in ready:
            ordered.append(tickets[key])
            resolved.add(key)
            pending.remove(key)
    return ordered


def _existing_sources(tracker: FileBoardTracker) -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for path in tracker.board_root.glob("*.md"):
        front, body = parse_ticket_file(path)
        match = _SOURCE_RE.search(body)
        identifier = front.get("id") or front.get("identifier")
        if "<!-- symphony-factory-source:" in body and not match:
            raise ValueError(f"{path}: malformed factory provenance marker")
        if match and identifier:
            key = match.group(1)
            if key in found:
                raise ValueError(f"duplicate board provenance for Wayfinder id {key}")
            found[key] = (str(identifier), match.group(2), match.group(3))
    return found


def _managed_body(ticket: WayfinderTicket, identifiers: dict[str, str], root: Path) -> str:
    deps = _dependency_identifiers(ticket, identifiers)
    dependency_section = ""
    if deps:
        dependency_section = "\n\n## Dependencies\n\n" + "\n".join(f"- {item}" for item in deps)
    managed = (
        f"Route: {ticket.route}\n\n{ticket.description.strip()}"
        f"{dependency_section}\n{_END_MARKER}"
    )
    source_hash = hashlib.sha256(ticket.source_path.read_bytes()).hexdigest()
    managed_hash = _managed_hash(
        ticket.title,
        ["factory", f"route:{ticket.route.lower()}"],
        list(ticket.skills),
        deps,
        managed,
    )
    return (
        f"<!-- symphony-factory-source: id={ticket.key} "
        f"path={ticket.source_path.relative_to(root).as_posix()} "
        f"sha256={source_hash} managed_sha256={managed_hash} -->\n{managed}"
    )


def _dependency_identifiers(
    ticket: WayfinderTicket, identifiers: dict[str, str]
) -> list[str]:
    return [identifiers[key] for key in ticket.blocked_by]


def _managed_hash(
    title: object, labels: object, skills: object, blocked_by: object, managed: str
) -> str:
    payload = repr((title, labels, skills, blocked_by, managed)).encode()
    return hashlib.sha256(payload).hexdigest()


def _assert_managed_region_untouched(identifier: str, front: dict, body: str) -> None:
    match = _SOURCE_RE.search(body)
    if match is None or _END_MARKER not in body:
        raise ValueError(f"{identifier}: factory managed boundary is malformed")
    managed_hash = match.group(4)
    if managed_hash is None:
        raise ValueError(
            f"{identifier}: legacy factory card cannot be safely refreshed; recreate it"
        )
    managed = body[match.end() : body.index(_END_MARKER) + len(_END_MARKER)].lstrip("\n")
    actual_hash = _managed_hash(
        front.get("title"),
        front.get("labels"),
        front.get("skills"),
        front.get("blocked_by", []),
        managed,
    )
    if actual_hash != managed_hash:
        raise ValueError(f"{identifier}: factory managed fields or region were edited")


def _refresh_ready_ticket(
    tracker: FileBoardTracker,
    identifier: str,
    ticket: WayfinderTicket,
    identifiers: dict[str, str],
    root: Path,
) -> None:
    def mutate(front: dict, body: str):
        if str(front.get("state", "")).lower() != "ready":
            return None
        if _END_MARKER not in body:
            raise ValueError(
                f"{identifier}: factory provenance exists but managed end marker is missing"
            )
        _assert_managed_region_untouched(identifier, front, body)
        suffix = body.split(_END_MARKER, 1)[1].strip()
        managed = _managed_body(ticket, identifiers, root)
        refreshed_body = "\n\n".join(part for part in (managed, suffix) if part)
        labels = ["factory", f"route:{ticket.route.lower()}"]
        skills = list(ticket.skills)
        blocked_by = _dependency_identifiers(ticket, identifiers)
        if (
            front.get("title") == ticket.title
            and front.get("labels") == labels
            and front.get("skills") == skills
            and front.get("blocked_by", []) == blocked_by
            and body.strip() == refreshed_body
        ):
            return None
        front["title"] = ticket.title
        front["labels"] = labels
        front["skills"] = skills
        if blocked_by:
            front["blocked_by"] = blocked_by
        else:
            front.pop("blocked_by", None)
        front["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return front, refreshed_body

    tracker._mutate_ticket(identifier, mutate)  # type: ignore[attr-defined]
