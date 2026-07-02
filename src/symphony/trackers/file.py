"""File-based Kanban tracker (SPEC §11 — non-Linear adapter).

Each ticket is one Markdown file under `tracker.board_root` with YAML front
matter that holds tracker fields. The Markdown body is the description.

Format:

    ---
    id: DEV-001
    title: Fix the foo
    state: Todo
    priority: 2
    labels: [backend, bug]
    blocked_by:
      - identifier: DEV-099
        state: Todo
    created_at: 2026-05-08T10:00:00Z
    updated_at: 2026-05-08T10:00:00Z
    ---

    Description body in Markdown...

Conventions:
- `id` and `identifier` are the same value (filesystem-friendly key).
- `state` strings are matched against `tracker.active_states` /
  `tracker.terminal_states` after lower-casing (per §4.2 normalization).
- File names SHOULD be `<id>.md`, but any `*.md` file is scanned.
- The orchestrator only reads. Ticket writes (state transitions, comments)
  are done by the coding agent — see §11.5 — typically by overwriting the
  ticket file via its built-in shell/file tools.
"""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback.
    fcntl = None  # type: ignore[assignment]

import yaml

from ..errors import LinearUnknownPayload, SymphonyError
from ..issue import (
    BlockerRef,
    Issue,
    coerce_priority,
    normalize_labels,
    normalize_state,
    parse_iso_timestamp,
)
from ..skills import normalize_skill_names
from ..workflow import TrackerConfig


_FRONT_MATTER_DELIM = "---"
_YAML_TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:")
_MARKDOWN_SECTION_START = re.compile(r"^\s*(#{1,6}\s|```)")
_CANONICAL_FRONT_MATTER_KEYS = {
    "id",
    "identifier",
    "title",
    "state",
    "priority",
    "branch_name",
    "url",
    "labels",
    "blocked_by",
    "agent",
    "agent_kind",
    "skills",
    "created_at",
    "updated_at",
}
_LOCK_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_GENERATED_ID_ATTEMPTS = 100
_TicketMutation = Callable[
    [dict[str, Any], str], tuple[dict[str, Any], str] | None
]
_CasToken = tuple[Any, int | None]


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if os.name != "posix" or fcntl is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _lock_name(value: str) -> str:
    return _LOCK_NAME_RE.sub("_", value).strip("._") or "ticket"


# ---------------------------------------------------------------------------
# Parsing / serialization
# ---------------------------------------------------------------------------


def parse_ticket_file(path: Path) -> tuple[dict[str, Any], str]:
    """Return (front_matter_dict, body_text)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIM:
        return {}, text.rstrip()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FRONT_MATTER_DELIM)
    except StopIteration as exc:
        raise SymphonyError(
            "ticket front matter not terminated", path=str(path)
        ) from exc
    front_text = "\n".join(lines[1:end])
    try:
        parsed = yaml.safe_load(front_text)
    except yaml.YAMLError as exc:
        healed = _auto_heal_markdown_in_front_matter(path, lines, end)
        if healed is not None:
            return healed
        raise SymphonyError(
            "invalid YAML front matter", path=str(path), error=str(exc)
        ) from exc
    if parsed is None:
        front: dict[str, Any] = {}
    elif not isinstance(parsed, dict):
        raise SymphonyError("ticket front matter must be a map", path=str(path))
    else:
        front = parsed
    body = "\n".join(lines[end + 1 :]).strip()
    return front, body


def _auto_heal_markdown_in_front_matter(
    _path: Path, lines: list[str], end: int
) -> tuple[dict[str, Any], str] | None:
    """Repair a common ticket corruption: Markdown inserted before YAML close."""
    yaml_lines: list[str] = []
    misplaced_lines: list[str] = []
    in_misplaced_markdown = False

    for line in lines[1:end]:
        key_match = _YAML_TOP_LEVEL_KEY.match(line)
        is_canonical_key = (
            key_match is not None
            and key_match.group("key") in _CANONICAL_FRONT_MATTER_KEYS
        )

        if in_misplaced_markdown:
            if is_canonical_key:
                in_misplaced_markdown = False
                yaml_lines.append(line)
            else:
                misplaced_lines.append(line)
            continue

        if _MARKDOWN_SECTION_START.match(line):
            in_misplaced_markdown = True
            while yaml_lines and not yaml_lines[-1].strip():
                yaml_lines.pop()
            misplaced_lines.append(line)
            continue

        if not _looks_like_front_matter_line(line):
            return None
        yaml_lines.append(line)

    moved_text = "\n".join(misplaced_lines).strip()
    if not moved_text:
        return None

    yaml_text = "\n".join(yaml_lines)
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    if parsed is None:
        front: dict[str, Any] = {}
    elif not isinstance(parsed, dict):
        return None
    else:
        front = parsed

    original_body = "\n".join(lines[end + 1 :]).strip()
    body = "\n\n".join(part for part in (moved_text, original_body) if part)
    return front, body


def _looks_like_front_matter_line(line: str) -> bool:
    if not line.strip():
        return True
    if _YAML_TOP_LEVEL_KEY.match(line):
        return True
    return line.startswith((" ", "\t"))


def issue_from_file(path: Path) -> Issue | None:
    """Return None when the file lacks the required fields."""
    front, body = parse_ticket_file(path)
    raw_id = front.get("id") or front.get("identifier")
    title = front.get("title")
    state = front.get("state")
    if not (raw_id and title and state):
        return None
    identifier = str(raw_id)
    blockers = _parse_blockers(front.get("blocked_by"))
    return Issue(
        id=identifier,
        identifier=identifier,
        title=str(title),
        description=body or None,
        priority=coerce_priority(front.get("priority")),
        state=str(state),
        branch_name=str(front.get("branch_name") or "") or None,
        url=str(front.get("url") or "") or None,
        labels=normalize_labels(front.get("labels") or []),
        blocked_by=tuple(blockers),
        created_at=parse_iso_timestamp(front.get("created_at"))
        or parse_iso_timestamp(_file_ctime_iso(path)),
        updated_at=parse_iso_timestamp(front.get("updated_at"))
        or parse_iso_timestamp(_file_mtime_iso(path)),
        agent_kind=_parse_agent_kind(front),
        skills=normalize_skill_names(front.get("skills")),
    )


def _parse_agent_kind(front: dict[str, Any]) -> str | None:
    raw = front.get("agent_kind")
    if raw is None:
        agent = front.get("agent")
        if isinstance(agent, dict):
            raw = agent.get("kind")
    if not isinstance(raw, str):
        return None
    kind = raw.strip().lower()
    return kind or None


def _parse_blockers(value: Any) -> list[BlockerRef]:
    if not isinstance(value, list):
        return []
    out: list[BlockerRef] = []
    for entry in value:
        if isinstance(entry, str):
            out.append(BlockerRef(id=entry, identifier=entry, state=None))
        elif isinstance(entry, dict):
            ident = entry.get("identifier") or entry.get("id")
            if ident is None:
                continue
            out.append(
                BlockerRef(
                    id=str(entry.get("id") or ident),
                    identifier=str(ident),
                    state=(str(entry["state"]) if isinstance(entry.get("state"), str) else None),
                )
            )
    return out


def _file_ctime_iso(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()


def _file_mtime_iso(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


def _file_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def serialize_ticket(front: dict[str, Any], body: str) -> str:
    """Render a ticket file with stable key order."""
    ordered_keys = [
        "id",
        "identifier",
        "title",
        "state",
        "priority",
        "branch_name",
        "url",
        "labels",
        "blocked_by",
        "agent",
        "agent_kind",
        "skills",
        "created_at",
        "updated_at",
    ]
    ordered = {k: front[k] for k in ordered_keys if k in front and front[k] is not None}
    for k, v in front.items():
        if k not in ordered and v is not None:
            ordered[k] = v
    yaml_text = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).rstrip()
    body_text = (body or "").rstrip()
    parts = [_FRONT_MATTER_DELIM, yaml_text, _FRONT_MATTER_DELIM]
    if body_text:
        parts.append("")
        parts.append(body_text)
    return "\n".join(parts) + "\n"


_WARNING_HEADING_RE = re.compile(
    r"^##\s+(?:Conflict|Budget\s+Exceeded)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_warning_blocks(body: str) -> str:
    """G5 — remove orchestrator-authored `## Conflict` / `## Budget Exceeded`
    sections from a ticket body.

    A section runs from its `##` heading up to (but not including) the next
    `##` heading or end-of-body. Operator-authored bodies that happen to
    use the same headings sit alongside whatever introduction the operator
    wrote, so the strip only removes the heading + content up to the next
    heading and leaves surrounding paragraphs intact.
    """
    matches = list(_WARNING_HEADING_RE.finditer(body))
    if not matches:
        return body
    # Walk backwards so deletions don't invalidate earlier offsets.
    out = body
    for match in reversed(matches):
        start = match.start()
        # Find the next `## ` heading after this one.
        next_heading = re.search(r"^##\s+\S", out[match.end():], re.MULTILINE)
        end = match.end() + next_heading.start() if next_heading else len(out)
        out = out[:start] + out[end:]
    return out.rstrip() + ("\n" if body.endswith("\n") else "")


def write_ticket_atomic(path: Path, front: dict[str, Any], body: str) -> None:
    """Atomic write: temp file in same dir + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialize_ticket(front, body))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# TrackerClient implementation
# ---------------------------------------------------------------------------


class FileBoardTracker:
    """Adapter over a directory of Markdown ticket files."""

    def __init__(self, tracker: TrackerConfig) -> None:
        if tracker.board_root is None:
            raise LinearUnknownPayload("board_root not configured")
        self._root = tracker.board_root.resolve()
        self._active = {s.lower() for s in tracker.active_states}
        self._terminal = {s.lower() for s in tracker.terminal_states}
        self._root.mkdir(parents=True, exist_ok=True)

    def _lock_path(self, name: str) -> Path:
        return self._root / ".locks" / f"{_lock_name(name)}.lock"

    def _allocator_lock_path(self) -> Path:
        return self._lock_path("allocator")

    def _ticket_lock_path(self, identifier: str) -> Path:
        return self._lock_path(identifier)

    def close(self) -> None:
        return None

    def __enter__(self) -> "FileBoardTracker":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @property
    def board_root(self) -> Path:
        return self._root

    # §11.1.1
    def fetch_candidate_issues(self) -> list[Issue]:
        return [
            i
            for i in self._scan_all()
            if normalize_state(i.state) in self._active
            and normalize_state(i.state) not in self._terminal
        ]

    # §11.1.2
    def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        wanted = {s.lower() for s in state_names if s}
        if not wanted:
            return []
        return [i for i in self._scan_all() if normalize_state(i.state) in wanted]

    # §11.1.3
    def fetch_issue_states_by_ids(self, ids: Iterable[str]) -> list[Issue]:
        targets = {i for i in ids if i}
        if not targets:
            return []
        out: list[Issue] = []
        for issue in self._scan_all():
            if issue.id in targets:
                out.append(
                    Issue(
                        id=issue.id,
                        identifier=issue.identifier,
                        title=issue.title,
                        description=None,
                        priority=None,
                        state=issue.state,
                        agent_kind=issue.agent_kind,
                    )
                )
        return out

    def fetch_issue_full_by_id(self, issue_id: str) -> Issue | None:
        """Return the ticket with full markdown body for ``issue_id``.

        Used by the stage-contract validator — the minimal `fetch_issue_
        states_by_ids` strips description, which would make every contract
        evaluation see an empty body. `_scan_all` already returns issues
        hydrated by `issue_from_file`, so this method is cheap on top.
        """
        if not issue_id:
            return None
        for issue in self._scan_all():
            if issue.id == issue_id:
                return issue
        return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _scan_all(self) -> list[Issue]:
        out: list[Issue] = []
        for path in sorted(self._root.glob("*.md")):
            try:
                issue = issue_from_file(path)
            except SymphonyError:
                continue
            if issue is not None:
                out.append(issue)
        return _hydrate_blocker_states(out)

    # ------------------------------------------------------------------
    # convenience helpers used by board CLI / agent tool
    # ------------------------------------------------------------------

    def find_path(self, identifier: str) -> Path | None:
        candidate = self._root / f"{identifier}.md"
        if candidate.exists():
            return candidate
        for path in self._root.glob("*.md"):
            try:
                front, _ = parse_ticket_file(path)
            except SymphonyError:
                continue
            raw_id = front.get("id") or front.get("identifier")
            if raw_id and str(raw_id) == identifier:
                return path
        return None

    def _mutate_ticket(
        self,
        identifier: str,
        mutate: _TicketMutation,
        *,
        missing_ok: bool = False,
    ) -> Path | None:
        path = self.find_path(identifier)
        if path is None:
            if missing_ok:
                return None
            raise SymphonyError("ticket not found", identifier=identifier)
        with _exclusive_lock(self._ticket_lock_path(identifier)):
            path = self.find_path(identifier)
            if path is None:
                if missing_ok:
                    return None
                raise SymphonyError("ticket not found", identifier=identifier)
            seen_mtime_ns = _file_mtime_ns(path)
            front, body = parse_ticket_file(path)
            seen_token = (front.get("updated_at"), seen_mtime_ns)
            result = mutate(front, body)
            if result is None:
                return path
            new_front, new_body = result
            self._write_ticket_with_updated_at_cas(
                path, seen_token, mutate, new_front, new_body
            )
            return path

    def _write_ticket_with_updated_at_cas(
        self,
        path: Path,
        seen_token: _CasToken,
        mutate: _TicketMutation,
        front: dict[str, Any],
        body: str,
    ) -> None:
        for _ in range(3):
            latest_front, latest_body = parse_ticket_file(path)
            latest_token = (latest_front.get("updated_at"), _file_mtime_ns(path))
            if latest_token == seen_token:
                write_ticket_atomic(path, front, body)
                return
            seen_token = latest_token
            result = mutate(latest_front, latest_body)
            if result is None:
                return
            front, body = result
        write_ticket_atomic(path, front, body)

    def transition(self, identifier: str, new_state: str) -> Path:
        def mutate(front: dict[str, Any], body: str) -> tuple[dict[str, Any], str]:
            front["state"] = new_state
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return front, body

        path = self._mutate_ticket(identifier, mutate)
        assert path is not None
        return path

    def update_state(self, issue: Issue, target_state: str) -> None:
        """TrackerClient protocol mutation hook.

        G5 — when the target_state is one of the configured active states,
        strip any orchestrator-authored `## Conflict` / `## Budget Exceeded`
        sections so board UIs do not keep showing warnings that no longer
        apply. Operator-authored body content with the same headings is
        preserved because the strip is gated on transition direction and
        only fires on transition into an active state.
        """
        if target_state.lower() in self._active:
            self._strip_orchestrator_warning_sections(issue.identifier)
        self.transition(issue.identifier, target_state)

    def _strip_orchestrator_warning_sections(self, identifier: str) -> None:
        def mutate(
            front: dict[str, Any], body: str
        ) -> tuple[dict[str, Any], str] | None:
            stripped = _strip_warning_blocks(body)
            if stripped == body:
                return None
            return front, stripped

        self._mutate_ticket(identifier, mutate, missing_ok=True)

    def append_note(self, issue: Issue, heading: str, body: str) -> None:
        """Append an orchestrator-authored Markdown note to a ticket file."""
        clean_heading = heading.strip().lstrip("#").strip() or "Note"
        clean_body = body.strip()

        def mutate(
            front: dict[str, Any], existing_body: str
        ) -> tuple[dict[str, Any], str]:
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            note = f"## {clean_heading}"
            if clean_body:
                note = f"{note}\n\n{clean_body}"
            combined = "\n\n".join(
                part for part in (existing_body.strip(), note) if part
            )
            return front, combined

        self._mutate_ticket(issue.identifier, mutate)

    def record_agent_kind(self, identifier: str, agent_kind: str) -> Path | None:
        """Write ``agent_kind`` to ticket frontmatter when missing.

        Idempotent and preserves any existing override — recognized in
        both ``agent_kind:`` (flat) and ``agent.kind:`` (nested) forms via
        ``_parse_agent_kind`` so either user-authored shape is honored.
        New writes use the nested shape to match :meth:`create`.
        ``updated_at`` bumps only when the file is actually modified.
        """
        normalized = agent_kind.strip().lower()

        def mutate(
            front: dict[str, Any], body: str
        ) -> tuple[dict[str, Any], str] | None:
            if _parse_agent_kind(front) or not normalized:
                return None
            front["agent"] = {"kind": normalized}
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return front, body

        return self._mutate_ticket(identifier, mutate, missing_ok=True)

    def next_identifier(self, prefix: str) -> str:
        """`<PREFIX>-<n+1>` where n is the highest existing number for prefix."""
        with _exclusive_lock(self._allocator_lock_path()):
            return self._next_identifier_unlocked(prefix)

    def _next_identifier_unlocked(self, prefix: str) -> str:
        highest = 0
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
        for path in self._root.glob("*.md"):
            match = pattern.match(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}-{highest + 1}"

    def create_with_next_identifier(
        self,
        prefix: str,
        *,
        title: str,
        state: str = "Todo",
        priority: int | None = None,
        labels: list[str] | None = None,
        description: str = "",
        agent_kind: str | None = None,
        skills: list[str] | None = None,
    ) -> tuple[str, Path]:
        with _exclusive_lock(self._allocator_lock_path()):
            last_error: Exception | None = None
            for _ in range(_GENERATED_ID_ATTEMPTS):
                identifier = self._next_identifier_unlocked(prefix)
                try:
                    path = self.create(
                        identifier=identifier,
                        title=title,
                        state=state,
                        priority=priority,
                        labels=labels,
                        description=description,
                        agent_kind=agent_kind,
                        skills=skills,
                    )
                except SymphonyError as exc:
                    last_error = exc
                    continue
                return identifier, path
        raise last_error or SymphonyError("could not allocate ticket id", prefix=prefix)

    def create(
        self,
        *,
        identifier: str,
        title: str,
        state: str = "Todo",
        priority: int | None = None,
        labels: list[str] | None = None,
        description: str = "",
        agent_kind: str | None = None,
        skills: list[str] | None = None,
    ) -> Path:
        path = self._root / f"{identifier}.md"
        with _exclusive_lock(self._ticket_lock_path(identifier)):
            if path.exists():
                raise SymphonyError("ticket already exists", identifier=identifier)
            front = self._new_ticket_front(
                identifier=identifier,
                title=title,
                state=state,
                priority=priority,
                labels=labels,
                agent_kind=agent_kind,
                skills=skills,
            )
            write_ticket_atomic(path, front, description)
            return path

    def _new_ticket_front(
        self,
        *,
        identifier: str,
        title: str,
        state: str,
        priority: int | None,
        labels: list[str] | None,
        agent_kind: str | None,
        skills: list[str] | None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        front: dict[str, Any] = {
            "id": identifier,
            "identifier": identifier,
            "title": title,
            "state": state,
            "priority": priority,
            "labels": list(labels or []),
            "created_at": now,
            "updated_at": now,
        }
        if isinstance(agent_kind, str) and agent_kind.strip():
            front["agent"] = {"kind": agent_kind.strip().lower()}
        normalized_skills = normalize_skill_names(list(skills or []))
        if normalized_skills:
            front["skills"] = list(normalized_skills)
        return front

    def update_fields(
        self,
        identifier: str,
        *,
        title: str | None = None,
        description: str | None = None,
        state: str | None = None,
        priority: int | None = None,
        clear_priority: bool = False,
        labels: list[str] | None = None,
        skills: list[str] | None = None,
        agent_kind: str | None = None,
    ) -> Path:
        """Partial ticket update from the board UI. None = leave unchanged.

        `description` replaces the Markdown body. `agent_kind=""` clears the
        per-ticket agent override; `clear_priority=True` drops priority.
        """
        def mutate(front: dict[str, Any], body: str) -> tuple[dict[str, Any], str]:
            if title is not None:
                front["title"] = title
            if state is not None:
                front["state"] = state
            if priority is not None:
                front["priority"] = priority
            elif clear_priority:
                front.pop("priority", None)
            if labels is not None:
                front["labels"] = [str(item) for item in labels]
            if skills is not None:
                normalized = normalize_skill_names(skills)
                if normalized:
                    front["skills"] = list(normalized)
                else:
                    front.pop("skills", None)
            if agent_kind is not None:
                cleaned = agent_kind.strip().lower()
                front.pop("agent_kind", None)
                if cleaned:
                    front["agent"] = {"kind": cleaned}
                else:
                    front.pop("agent", None)
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return front, body if description is None else description

        path = self._mutate_ticket(identifier, mutate)
        assert path is not None
        return path

    def delete(self, identifier: str) -> Path:
        path = self.find_path(identifier)
        if path is None:
            raise SymphonyError("ticket not found", identifier=identifier)
        path.unlink()
        return path


def _hydrate_blocker_states(issues: list[Issue]) -> list[Issue]:
    current_state_by_id = {issue.identifier: issue.state for issue in issues}
    hydrated: list[Issue] = []
    for issue in issues:
        blockers: list[BlockerRef] = []
        changed = False
        for blocker in issue.blocked_by:
            key = blocker.identifier or blocker.id
            current_state = current_state_by_id.get(key or "")
            if current_state is not None and current_state != blocker.state:
                blockers.append(replace(blocker, state=current_state))
                changed = True
            else:
                blockers.append(blocker)
        if changed:
            hydrated.append(replace(issue, blocked_by=tuple(blockers)))
        else:
            hydrated.append(issue)
    return hydrated
