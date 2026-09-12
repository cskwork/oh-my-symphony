"""Intent proposals: the single human gate of the chat-to-delivery cycle.

An *Intent proposal* is a server-owned chat action created only from the
strict ``<symphony-intent>`` marker the chat agent emits after its human
explanation. *Intent approval* is the operator's explicit confirmation of one
proposal; it files the request ticket through the tracker API and records
``.sdlc/work/<slug>/intent.md`` beside the workflow so the decision survives
in the project. Nothing here runs agent code, and the agent never files the
request ticket itself: the operator approves, the server writes.

The intent markdown follows the sdlc-kit stage-1 shape so the pipeline's
Intake lane can consume it verbatim: ``## Problem``, ``## Evidence``,
``## Success criteria`` (checkbox lines), ``## Out of scope``,
``## Constraints`` and ``## Open questions``. The ``track`` is ``full``
(Intake through Document) or ``micro`` (Intake skips Research).

Every proposal is scanned with the sdlc-kit trip-wire heuristics
(``tools/tripwire.sh``: migrations, data deletion, public API, security
paths, infra/config). Hits never block the operator — they are shown on the
card and recorded on the ticket and in ``intent.md`` so the pipeline's Review
lane treats them as mandatory objection candidates — but a hit downgrades a
``micro`` track to ``full``, exactly as the kit's stage 1 requires a
trip-wire-clean intent for the micro track.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .errors import ChatIntentActionError

if TYPE_CHECKING:
    from .workflow import ServiceConfig

INTENT_OPEN = "<symphony-intent>"
INTENT_CLOSE = "</symphony-intent>"
INTENT_TRACKS: tuple[str, ...] = ("full", "micro")
INTENT_REQUIRED_HEADINGS: tuple[str, ...] = (
    "## Problem",
    "## Success criteria",
    "## Out of scope",
)
INTENT_TICKET_PREFIX = "REQ"
INTENT_TTL = timedelta(minutes=30)
INTENT_MAX_TITLE = 200
INTENT_MAX_BODY = 32 * 1024
_MAX_PAYLOAD = INTENT_MAX_BODY + 2 * 1024
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_CHECKBOX_RE = re.compile(r"^\s*- \[ \] \S", re.M)
_INTAKE = "intake"

# sdlc-kit `tools/tripwire.sh` patterns, verbatim (extended regex, case-
# insensitive). A hit is a question for the reviewer, not a conviction.
TRIPWIRE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("migration/schema", r"migrat|schema change|ALTER TABLE|CREATE TABLE|DROP TABLE|[.]sql"),
    ("data deletion", r"DELETE FROM|DROP |TRUNCATE|destructive|backfill|rm -rf"),
    ("public API", r"public API|breaking change|API contract|openapi|swagger|/api/v[0-9]"),
    ("security paths", r"auth|secret|credential|password|token|permission|session"),
    (
        "infra/config",
        r"Dockerfile|docker-compose|[.]github/workflows|terraform|helm|kubernetes|k8s|nginx|systemd|deploy",
    ),
)
_TRIPWIRE_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE)) for label, pattern in TRIPWIRE_PATTERNS
)
TRIPWIRE_MAX_LINES = 3


@dataclass(frozen=True)
class TripwireHit:
    """One trip-wire category that matched, with up to three matching lines."""

    label: str
    lines: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "lines": list(self.lines)}


def scan_tripwires(text: str) -> tuple[TripwireHit, ...]:
    """Heuristic trip-wire scan of an intent body (kit `tools/tripwire.sh`)."""

    hits: list[TripwireHit] = []
    lines = [line.strip() for line in (text or "").splitlines()]
    for label, pattern in _TRIPWIRE_COMPILED:
        matched = [line for line in lines if line and pattern.search(line)]
        if matched:
            hits.append(TripwireHit(label=label, lines=tuple(matched[:TRIPWIRE_MAX_LINES])))
    return tuple(hits)


def render_tripwire_section(hits: tuple[TripwireHit, ...]) -> str:
    """`## Trip-wires` markdown for the ticket body and `intent.md`."""

    if not hits:
        return "## Trip-wires\n\n- none (heuristic scan; the Review lane still judges)\n"
    rows = "\n".join(f"- {hit.label}: `{line}`" for hit in hits for line in hit.lines)
    return (
        "## Trip-wires\n\n"
        "Heuristic hits from the sdlc-kit scan. Each one is a mandatory "
        "objection candidate for the Review lane, not a conviction.\n\n"
        f"{rows}\n"
    )


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expiry() -> str:
    return (datetime.now(timezone.utc) + INTENT_TTL).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_expired(expires_at: str) -> bool:
    try:
        deadline = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return True
    return datetime.now(timezone.utc) >= deadline


def strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate members at every depth."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


@dataclass
class IntentAction:
    """One server-owned Intent proposal shown as a card in the chat."""

    action_id: str
    slug: str
    title: str
    track: str
    intent: str
    status: str = "pending"
    ticket: dict[str, Any] | None = None
    error: str | None = None
    expires_at: str = field(default_factory=_expiry)
    tripwires: tuple[TripwireHit, ...] = ()
    task: Any = field(default=None, repr=False, compare=False)

    def is_expired(self) -> bool:
        return _is_expired(self.expires_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "slug": self.slug,
            "title": self.title,
            "track": self.track,
            "intent": self.intent,
            "status": self.status,
            "ticket": self.ticket,
            "error": self.error,
            "expires_at": self.expires_at,
            "tripwires": [hit.as_dict() for hit in self.tripwires],
        }


def parse_intent_marker(text: str) -> tuple[str, IntentAction | None]:
    """Extract one strictly shaped Intent proposal from agent text.

    Ordinary prose remains ordinary chat. Only the documented marker with the
    exact four members becomes a card, so no later reply can turn arbitrary
    model output into a board write.
    """

    # Count first: a dot-star regex over backend-controlled output would
    # rescan quadratically on repeated unmatched opening tags.
    if text.count(INTENT_OPEN) != 1 or text.count(INTENT_CLOSE) != 1:
        return text, None
    open_start = text.find(INTENT_OPEN)
    start = open_start + len(INTENT_OPEN)
    end = text.find(INTENT_CLOSE)
    if end < start:
        return text, None
    payload = text[start:end].strip()
    if len(payload) > _MAX_PAYLOAD:
        return text, None
    try:
        value = json.loads(payload, object_pairs_hook=strict_json_object)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return text, None
    if not isinstance(value, dict) or set(value) != {"slug", "title", "track", "intent"}:
        return text, None
    slug = value.get("slug")
    title = value.get("title")
    track = value.get("track")
    intent = value.get("intent")
    if (
        not isinstance(slug, str)
        or not _SLUG_RE.fullmatch(slug)
        or not isinstance(title, str)
        or not 0 < len(title.strip()) <= INTENT_MAX_TITLE
        or not isinstance(track, str)
        or track not in INTENT_TRACKS
        or not isinstance(intent, str)
        or not 0 < len(intent.strip()) <= INTENT_MAX_BODY
    ):
        return text, None
    body = intent.strip()
    if any(heading not in body for heading in INTENT_REQUIRED_HEADINGS):
        return text, None
    if _CHECKBOX_RE.search(body) is None:
        return text, None
    visible = (text[:open_start] + text[end + len(INTENT_CLOSE) :]).strip()
    tripwires = scan_tripwires(body)
    if tripwires and track == "micro":
        # Kit stage 1: the micro track requires a trip-wire-clean intent.
        track = "full"
    return visible, IntentAction(
        action_id="intent-" + uuid.uuid4().hex,
        slug=slug,
        title=title.strip(),
        track=track,
        intent=body,
        tripwires=tripwires,
    )


def intent_target_state(cfg: ServiceConfig) -> str:
    """Intake when the board has that lane, else the first active state."""

    states = list(cfg.tracker.active_states)
    for state in states:
        if state.strip().lower() == _INTAKE:
            return state
    if not states:
        raise ChatIntentActionError("the board has no active states to file into")
    return states[0]


def intent_artifact_path(cfg: ServiceConfig, slug: str) -> Path:
    """`.sdlc/work/<slug>/intent.md` beside the workflow (sdlc-kit layout)."""

    return cfg.workflow_path.parent / ".sdlc" / "work" / slug / "intent.md"


def render_ticket_description(
    action: IntentAction, *, approved_at: str, session_id: str
) -> str:
    """The request ticket body: the intent plus its track and approval facts."""

    return (
        f"{action.intent}\n\n"
        f"{render_tripwire_section(action.tripwires)}\n"
        f"## Track\n\n{action.track}\n\n"
        "## Approval\n\n"
        f"- Approved at: {approved_at}\n"
        f"- Chat session: {session_id}\n"
        f"- Intent artifact: .sdlc/work/{action.slug}/intent.md\n"
    )


def render_intent_markdown(
    action: IntentAction,
    *,
    approved_at: str,
    session_id: str,
    ticket_identifier: str,
) -> str:
    """The durable `intent.md` record in the sdlc-kit stage-1 shape."""

    return (
        f"# Intent: {action.slug}\n\n"
        f"- Date: {approved_at[:10]}\n"
        f"- Title: {action.title}\n"
        f"- Track: {action.track}\n"
        f"- Requested by: chat session {session_id}\n\n"
        f"{action.intent}\n\n"
        f"{render_tripwire_section(action.tripwires)}\n"
        "## Approval\n\n"
        f"- Approved at: {approved_at}\n"
        f"- Ticket: {ticket_identifier}\n"
        "- Gate: intent (the only human gate; later stages run unattended)\n"
    )


class IntentFiler(Protocol):
    """Server-owned writer that turns one approved intent into board work."""

    def __call__(
        self,
        cfg: ServiceConfig,
        action: IntentAction,
        *,
        approved_at: str,
        session_id: str,
    ) -> dict[str, Any]: ...


def file_intent_request(
    cfg: ServiceConfig,
    action: IntentAction,
    *,
    approved_at: str,
    session_id: str,
) -> dict[str, Any]:
    """Create the request ticket and write `intent.md`; return the ticket facts.

    The ticket goes through the tracker's validated create path (id allocation
    and DAG validation under the board lock). The intent artifact is written
    after the ticket exists so a failed filing leaves no orphan record.
    """

    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        raise ChatIntentActionError("intent approval requires a file board")
    from .trackers.file import FileBoardTracker

    state = intent_target_state(cfg)
    tracker = FileBoardTracker(cfg.tracker)
    try:
        identifier, path = tracker.create_validated(
            identifier=None,
            prefix=INTENT_TICKET_PREFIX,
            title=action.title,
            state=state,
            description=render_ticket_description(
                action, approved_at=approved_at, session_id=session_id
            ),
            request=action.slug,
        )
    finally:
        tracker.close()
    artifact = intent_artifact_path(cfg, action.slug)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        render_intent_markdown(
            action,
            approved_at=approved_at,
            session_id=session_id,
            ticket_identifier=identifier,
        ),
        encoding="utf-8",
    )
    return {
        "identifier": identifier,
        "state": state,
        "request": action.slug,
        "path": str(path),
        "intent_path": str(artifact),
    }
