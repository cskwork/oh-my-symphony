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

import errno
import os
import re
import tempfile
import time
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
from ..logging import get_logger
from ..skills import normalize_skill_names
from ..ticket_markdown import parse_body_dependency_ids
from ..workflow import TrackerConfig
from .validate import (
    board_edges,
    dangling_blockers,
    find_cycle,
    validate_identifier,
)


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
    "request",
    "agent",
    "agent_kind",
    "last_agent_kind",
    "skills",
    "created_at",
    "updated_at",
}
_LOCK_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_TRACKER_TEMP_NAME_RE = re.compile(
    r"^\.tmp-symphony-ticket-[a-z0-9_]{8}\.tmp$"
)
_GENERATED_ID_ATTEMPTS = 100
_STALE_TEMP_AGE_SECONDS = 60.0
_TicketMutation = Callable[
    [dict[str, Any], str], tuple[dict[str, Any], str] | None
]
_CasToken = tuple[Any, int | None]
log = get_logger()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if os.name == "nt":
        # fcntl is POSIX-only; the same exclusive cross-process lock is
        # expressed with the Windows byte-range lock so concurrent board
        # mutations serialize on Windows too. LK_NBLCK is polled because
        # LK_LOCK gives up after ~10 one-second attempts.
        import msvcrt

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as lock:
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    time.sleep(0.02)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is None:  # pragma: no cover - non-Windows fallback
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
        healed = _parse_front_matter_prefix_without_delimiters(path, lines)
        if healed is not None:
            return healed
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
        healed = _auto_heal_misindented_top_level_front_matter(path, lines, end)
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


def _parse_front_matter_prefix_without_delimiters(
    path: Path, lines: list[str]
) -> tuple[dict[str, Any], str] | None:
    """Recover tickets where an agent dropped only the YAML delimiters."""
    if not lines:
        return None
    first_key = _YAML_TOP_LEVEL_KEY.match(lines[0])
    if first_key is None or first_key.group("key") not in _CANONICAL_FRONT_MATTER_KEYS:
        return None

    yaml_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            body_start = index + 1
            break
        if _MARKDOWN_SECTION_START.match(line):
            body_start = index
            break
        key_match = _YAML_TOP_LEVEL_KEY.match(line)
        if (
            key_match is not None
            and key_match.group("key") not in _CANONICAL_FRONT_MATTER_KEYS
        ):
            return None
        if not _looks_like_front_matter_line(line):
            body_start = index
            break
        yaml_lines.append(line)
        body_start = index + 1
    if not yaml_lines:
        return None

    try:
        parsed = yaml.safe_load("\n".join(yaml_lines))
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict) or not parsed.get("state"):
        return None

    front = dict(parsed)
    identifier = str(front.get("id") or front.get("identifier") or path.stem)
    front.setdefault("id", identifier)
    front.setdefault("identifier", identifier)
    front.setdefault("title", identifier)
    body = "\n".join(lines[body_start:]).strip()
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


def _auto_heal_misindented_top_level_front_matter(
    _path: Path, lines: list[str], end: int
) -> tuple[dict[str, Any], str] | None:
    """Read tickets where an agent indented a canonical top-level key."""
    yaml_lines: list[str] = []
    changed = False
    for line in lines[1:end]:
        fixed = _unindent_misindented_top_level_key(line)
        changed = changed or fixed != line
        yaml_lines.append(fixed)
    if not changed:
        return None

    try:
        parsed = yaml.safe_load("\n".join(yaml_lines))
    except yaml.YAMLError:
        return None
    if parsed is None:
        front: dict[str, Any] = {}
    elif not isinstance(parsed, dict):
        return None
    else:
        front = parsed
    body = "\n".join(lines[end + 1 :]).strip()
    return front, body


def _unindent_misindented_top_level_key(line: str) -> str:
    stripped = line.lstrip(" ")
    indent = len(line) - len(stripped)
    if indent <= 0 or indent > 2:
        return line
    key_match = _YAML_TOP_LEVEL_KEY.match(stripped)
    if key_match and key_match.group("key") in _CANONICAL_FRONT_MATTER_KEYS:
        return stripped
    return line


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
    blockers = _merge_body_dependency_blockers(
        _parse_blockers(front.get("blocked_by")),
        body,
    )
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
        last_agent_kind=str(front.get("last_agent_kind") or "").strip().lower()
        or None,
        skills=normalize_skill_names(front.get("skills")),
        request=str(front.get("request") or "").strip() or None,
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


def _merge_body_dependency_blockers(
    blockers: list[BlockerRef], body: str | None
) -> list[BlockerRef]:
    merged = list(blockers)
    existing = {blocker.identifier or blocker.id for blocker in merged}
    for identifier in parse_body_dependency_ids(body):
        if identifier in existing:
            continue
        merged.append(BlockerRef(id=identifier, identifier=identifier, state=None))
        existing.add(identifier)
    return merged


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
        "request",
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
    r"^##\s+(?:Conflict|Budget\s+Exceeded|Blocked\s+(?:Fix|RCA))\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# A warning section ends at the next `##` heading *or* at a run-summary start
# marker. Without the second alternative, stripping a warning that sits just
# above a run summary would swallow the summary's opening marker and orphan
# the section, which would make the next upsert append a duplicate.
_WARNING_SECTION_END_RE = re.compile(
    r"^(?:##\s+\S|<!--\s*symphony-run:|<!--\s*symphony-artifacts\s*-->)",
    re.MULTILINE,
)

# Orchestrator-owned `## Artifacts` block. Marker-delimited so an upsert
# replaces its own previous block instead of appending a duplicate, and so
# an operator-authored `## Artifacts` heading elsewhere is never touched.
_ARTIFACTS_MARKER_OPEN = "<!-- symphony-artifacts -->"
_ARTIFACTS_MARKER_CLOSE = "<!-- /symphony-artifacts -->"
_ARTIFACTS_BLOCK_RE = re.compile(
    re.escape(_ARTIFACTS_MARKER_OPEN) + r".*?" + re.escape(_ARTIFACTS_MARKER_CLOSE),
    re.DOTALL,
)
_ARTIFACTS_MARKER_RE = re.compile(
    "|".join(
        re.escape(marker)
        for marker in (_ARTIFACTS_MARKER_OPEN, _ARTIFACTS_MARKER_CLOSE)
    )
)


def _strip_warning_blocks(body: str) -> str:
    """G5 — remove orchestrator-authored warning sections from a ticket body.

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
        # Find the next `## ` heading (or run-summary marker) after this one.
        next_heading = _WARNING_SECTION_END_RE.search(out[match.end():])
        end = match.end() + next_heading.start() if next_heading else len(out)
        out = out[:start] + out[end:]
    return out.rstrip() + ("\n" if body.endswith("\n") else "")


def write_ticket_atomic(path: Path, front: dict[str, Any], body: str) -> None:
    """Atomic write: temp file in same dir + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".tmp-symphony-ticket-", suffix=".tmp", dir=path.parent
    )
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


def _is_owned_tracker_temp(path: Path) -> bool:
    if _TRACKER_TEMP_NAME_RE.fullmatch(path.name) is not None:
        return True
    if not (path.name.startswith(".tmp-") and path.suffix == ".md"):
        return False
    try:
        return issue_from_file(path) is not None
    except (OSError, SymphonyError):
        return False


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
        self._last_graph_warning_signature: object = None
        self._sweep_stale_temps()

    def _lock_path(self, name: str) -> Path:
        return self._root / ".locks" / f"{_lock_name(name)}.lock"

    def _allocator_lock_path(self) -> Path:
        return self._lock_path("allocator")

    def _ticket_lock_path(self, identifier: str) -> Path:
        return self._lock_path(identifier)

    def _sweep_stale_temps(self) -> None:
        now = time.time()
        for path in self._root.glob(".tmp-*"):
            if not _is_owned_tracker_temp(path):
                continue
            try:
                age_seconds = now - path.stat().st_mtime
            except OSError:
                continue
            if age_seconds < _STALE_TEMP_AGE_SECONDS:
                continue
            try:
                path.unlink()
            except OSError as exc:
                log.warning(
                    "stale_tracker_temp_sweep_failed",
                    path=str(path),
                    error=str(exc),
                )
            else:
                log.warning(
                    "stale_tracker_temp_swept",
                    path=str(path),
                    age_seconds=age_seconds,
                )

    def _ticket_paths(self) -> list[Path]:
        return sorted(
            path
            for path in self._root.glob("*.md")
            if not path.name.startswith(".tmp-")
        )

    def list_all_identifiers(self) -> set[str]:
        """Every identifier with a ticket file, in any state.

        Used by the artifact sweep to tell "ticket left the board" from
        "ticket is merely not a dispatch candidate". The caller narrows this
        further by subtracting the archive state, so archived tickets do
        lose their deliverables once the TTL passes. Reads file stems rather
        than parsing bodies — one unparseable ticket must not make the sweep
        believe the ticket is gone.
        """
        return {path.stem for path in self._ticket_paths()}

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
        seen: dict[str, Path] = {}
        for path in self._ticket_paths():
            try:
                issue = issue_from_file(path)
            except SymphonyError as exc:
                log.warning("ticket_parse_skipped", path=str(path), error=str(exc))
                continue
            if issue is not None:
                kept_path = seen.get(issue.id)
                if kept_path is not None:
                    log.warning(
                        "duplicate_ticket_id_skipped",
                        identifier=issue.identifier,
                        kept_path=str(kept_path),
                        skipped_path=str(path),
                    )
                    continue
                seen[issue.id] = path
                out.append(issue)
        issues = _hydrate_blocker_states(out)
        self._warn_dependency_graph_issues(issues)
        return issues

    def scan_all(self) -> list[Issue]:
        """All parseable tickets, blocker states hydrated (any state)."""
        return self._scan_all()

    def _warn_dependency_graph_issues(self, issues: list[Issue]) -> None:
        """Load-time guardrail: WARN on dangling blockers / cycles.

        Never raises — degraded visibility beats a dead orchestrator poll
        loop. Identical findings are logged once until the board changes.
        """
        dangling = dangling_blockers(issues)
        cycle = find_cycle(board_edges(issues))
        signature = (
            tuple(sorted((k, tuple(v)) for k, v in dangling.items())),
            tuple(cycle or ()),
        )
        if signature == self._last_graph_warning_signature:
            return
        self._last_graph_warning_signature = signature
        for identifier, missing in sorted(dangling.items()):
            log.warning(
                "dangling_blocked_by",
                identifier=identifier,
                missing=", ".join(missing),
            )
        if cycle:
            log.warning("dependency_cycle", cycle=" -> ".join(cycle))

    # ------------------------------------------------------------------
    # convenience helpers used by board CLI / agent tool
    # ------------------------------------------------------------------

    def find_path(self, identifier: str) -> Path | None:
        candidate = self._root / f"{identifier}.md"
        if candidate.exists() and not candidate.name.startswith(".tmp-"):
            return candidate
        for path in self._ticket_paths():
            try:
                front, _ = parse_ticket_file(path)
            except SymphonyError as exc:
                log.warning("ticket_parse_skipped", path=str(path), error=str(exc))
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
        strip orchestrator-authored warning sections so board UIs do not keep
        showing warnings that no longer apply. Operator-authored body content
        with the same headings is preserved because the strip is gated on
        transition direction and only fires on transition into an active state.
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

    def upsert_artifacts_section(
        self, identifier: str, section_body: str
    ) -> Path | None:
        """Replace the orchestrator-owned `## Artifacts` block in a ticket.

        Idempotent: an unchanged block returns without a write, so the
        per-turn artifact collection never churns `updated_at` (which the
        auto-archive sweep reads as "someone touched this"). An empty
        `section_body` removes the block.

        `section_body` carries worker-authored text, so marker sequences are
        stripped from it here: a payload containing the close marker would
        end the non-greedy block match early and strand the real marker in
        the body, and every later upsert would strand another copy.
        Whitespace is renormalized only at the two splice seams — collapsing
        blank lines across the whole body would eat them inside fenced code
        blocks, where repro logs and diffs legitimately have them.
        """
        clean = _ARTIFACTS_MARKER_RE.sub("", section_body).strip()
        block = (
            f"{_ARTIFACTS_MARKER_OPEN}\n"
            f"## Artifacts\n\n{clean}\n"
            f"{_ARTIFACTS_MARKER_CLOSE}"
            if clean
            else ""
        )

        def mutate(
            front: dict[str, Any], body: str
        ) -> tuple[dict[str, Any], str] | None:
            existing = _ARTIFACTS_BLOCK_RE.search(body)
            if existing is None:
                if not block:
                    return None
                combined = "\n\n".join(part for part in (body.strip(), block) if part)
            else:
                if existing.group(0) == block:
                    return None
                head = body[: existing.start()].rstrip()
                tail = body[existing.end() :].strip("\n")
                combined = "\n\n".join(
                    part for part in (head, block, tail) if part
                )
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return front, combined

        return self._mutate_ticket(identifier, mutate, missing_ok=True)

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

    def record_last_agent_kind(self, identifier: str, agent_kind: str) -> Path | None:
        """Audit stamp: which backend last ran this ticket.

        F-20: on a `stage_kinds`-routed board the orchestrator must NOT write
        the `agent`/`agent_kind` pin (it would freeze the first lane's backend
        for the whole ticket), so the board previously lost the field
        entirely. `last_agent_kind` is never read by `_requested_agent_kind`,
        so it carries the audit value without becoming a pin.
        """
        normalized = agent_kind.strip().lower()

        def mutate(
            front: dict[str, Any], body: str
        ) -> tuple[dict[str, Any], str] | None:
            if not normalized or front.get("last_agent_kind") == normalized:
                return None
            front["last_agent_kind"] = normalized
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
        for path in self._ticket_paths():
            match = pattern.match(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}-{highest + 1}"


    def create_validated(
        self,
        *,
        identifier: str | None,
        prefix: str = "TASK",
        validate: "Callable[[list[Issue], str], None] | None" = None,
        title: str,
        state: str = "Todo",
        priority: int | None = None,
        labels: list[str] | None = None,
        description: str = "",
        agent_kind: str | None = None,
        skills: list[str] | None = None,
        blocked_by: list[str] | None = None,
        request: str | None = None,
    ) -> tuple[str, Path]:
        """Validate the board graph and create the ticket under one lock.

        F-15: validate-then-write was not atomic. `validate_ticket_dependencies`
        ran against a snapshot taken outside any lock while `create` took only
        a *per-ticket* lock, so two concurrent `symphony board new` calls (the
        deep Plan lane spawns several in a row, and workers may spawn
        sub-tickets in parallel) could each observe an acyclic board and
        jointly create a cycle — or race the id allocator.

        `identifier=None` allocates `<prefix>-<n+1>` inside the same lock.
        `validate` is called with the in-lock board snapshot and the resolved
        identifier; raising from it aborts before anything is written.
        """
        with _exclusive_lock(self._allocator_lock_path()):
            last_error: Exception | None = None
            attempts = 1 if identifier is not None else _GENERATED_ID_ATTEMPTS
            for _ in range(attempts):
                issues = self.scan_all()
                resolved = (
                    identifier
                    if identifier is not None
                    else self._next_identifier_unlocked(prefix)
                )
                if validate is not None:
                    validate(issues, resolved)
                try:
                    path = self.create(
                        identifier=resolved,
                        title=title,
                        state=state,
                        priority=priority,
                        labels=labels,
                        description=description,
                        agent_kind=agent_kind,
                        skills=skills,
                        blocked_by=blocked_by,
                        request=request,
                    )
                except SymphonyError as exc:
                    last_error = exc
                    if identifier is not None:
                        raise
                    continue
                return resolved, path
        raise last_error or SymphonyError("could not allocate ticket id", prefix=prefix)

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
        blocked_by: list[str] | None = None,
        request: str | None = None,
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
                        blocked_by=blocked_by,
                        request=request,
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
        blocked_by: list[str] | None = None,
        request: str | None = None,
    ) -> Path:
        identifier = validate_identifier(identifier)
        path = self._root / f"{identifier}.md"
        with _exclusive_lock(self._ticket_lock_path(identifier)):
            if self.find_path(identifier) is not None:
                raise SymphonyError("ticket already exists", identifier=identifier)
            front = self._new_ticket_front(
                identifier=identifier,
                title=title,
                state=state,
                priority=priority,
                labels=labels,
                agent_kind=agent_kind,
                skills=skills,
                blocked_by=blocked_by,
                request=request,
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
        blocked_by: list[str] | None = None,
        request: str | None = None,
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
        if blocked_by:
            front["blocked_by"] = [str(item) for item in blocked_by]
        if isinstance(request, str) and request.strip():
            front["request"] = request.strip()
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
        blocked_by: list[str] | None = None,
        request: str | None = None,
    ) -> Path:
        """Partial ticket update from the board UI. None = leave unchanged.

        `description` replaces the Markdown body. `agent_kind=""` clears the
        per-ticket agent override; `clear_priority=True` drops priority.
        `blocked_by=[]` clears the frontmatter blocker list; `request=""`
        clears the request grouping field.
        """
        identifier = validate_identifier(identifier)

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
            if blocked_by is not None:
                if blocked_by:
                    front["blocked_by"] = [str(item) for item in blocked_by]
                else:
                    front.pop("blocked_by", None)
            if request is not None:
                cleaned_request = request.strip()
                if cleaned_request:
                    front["request"] = cleaned_request
                else:
                    front.pop("request", None)
            front["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return front, body if description is None else description

        path = self._mutate_ticket(identifier, mutate)
        assert path is not None
        return path

    def delete(self, identifier: str) -> Path:
        with _exclusive_lock(self._ticket_lock_path(identifier)):
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
