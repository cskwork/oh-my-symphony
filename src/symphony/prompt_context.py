"""Deterministic ticket-body compaction for first-turn prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SECTION_HEADING_RE = re.compile(r"^(?P<marks>#{1,2})\s+(?P<title>.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SCAFFOLD_LINE_RE = re.compile(
    r"^(?:"
    r"[A-Z][A-Z0-9_-]*-\d+:\s+.*|"
    r"Current state:\s+.*|"
    r"Labels:\s+.*|"
    r"Blocked by:\s+.*|"
    r"Blockers:\s+.*"
    r")$",
    re.IGNORECASE,
)

_ORIGINAL_SCOPE_SECTIONS = {
    "acceptance criteria",
    "requirements",
    "scope",
    "dependencies",
}
_AGENT_OWNED_SECTIONS = {
    "triage",
    "reproduction",
    "plan",
    "done signals",
    "difficulty",
    "implementation",
    "self critique",
    "self-critique",
    "pipeline route",
    "security audit",
    "review",
    "review findings",
    "qa evidence",
    "qa failure",
    "ac scorecard",
    "merge status",
    "learnings",
    "wiki updates",
    "learn defect",
    "learn skipped",
    "human review",
    "as is to be report",
    "as-is to-be report",
    "evidence manifest",
    "changed files",
    "stage contract checklist",
    "contract checklist",
    "contract failure",
}
_FAILURE_SECTIONS = {
    "review findings",
    "qa failure",
    "learn defect",
    "contract failure",
}
_VERIFY_LATEST_SECTIONS = {
    "implementation",
    "evidence manifest",
    "qa evidence",
    "ac scorecard",
    "changed files",
    "stage contract checklist",
    "contract checklist",
}
_LEARN_LATEST_SECTIONS = {
    "implementation",
    "qa evidence",
    "ac scorecard",
    "merge status",
    "wiki updates",
    "docs notes",
    "documentation notes",
    "human review",
}
_IN_PROGRESS_LATEST_SECTIONS = {
    "triage",
}


@dataclass(frozen=True)
class TicketSection:
    heading: str
    title: str
    normalized_title: str
    body: str
    index: int

    def render(self) -> str:
        return f"{self.heading}{self.body}".strip()


def build_issue_prompt_context(
    issue: Any,
    *,
    state: str,
    is_rewind: bool = False,
) -> str:
    """Return the state-relevant ticket description for prompt rendering.

    This is intentionally a small Markdown section selector, not a Markdown
    renderer. It keeps the original user-facing scope plus the newest
    stage-critical sections and drops repeated stale history.
    """
    description = _issue_description(issue)
    preamble, sections = parse_ticket_sections(description)
    selected: dict[int, TicketSection] = {}

    for section in _leading_scope_sections(sections):
        selected.setdefault(section.index, section)
    for section in sections:
        if section.normalized_title in _ORIGINAL_SCOPE_SECTIONS:
            selected.setdefault(section.index, section)

    normalized_state = (state or "").strip().lower()
    if is_rewind:
        latest_failure = _latest_section(sections, _FAILURE_SECTIONS)
        if latest_failure is not None:
            selected[latest_failure.index] = latest_failure
    elif normalized_state == "verify":
        _select_latest_matching(selected, sections, _VERIFY_LATEST_SECTIONS)
    elif normalized_state in {"learn", "learning"}:
        _select_latest_matching(selected, sections, _LEARN_LATEST_SECTIONS)
    elif normalized_state == "in progress":
        _select_latest_matching(selected, sections, _IN_PROGRESS_LATEST_SECTIONS)
        latest_failure = _latest_section(sections, _FAILURE_SECTIONS)
        if latest_failure is not None:
            selected[latest_failure.index] = latest_failure
        latest_plan = _latest_section(sections, {"plan"})
        if latest_plan is not None and _plan_has_unresolved_work(latest_plan.body):
            selected[latest_plan.index] = latest_plan

    chunks: list[str] = []
    cleaned_preamble = _clean_preamble(preamble)
    if cleaned_preamble:
        chunks.append(cleaned_preamble)
    for section in sorted(selected.values(), key=lambda item: item.index):
        rendered = section.render()
        if rendered:
            chunks.append(rendered)
    if chunks:
        return "\n\n".join(chunks).strip()
    return _clean_preamble(description).strip()


def parse_ticket_sections(description: str | None) -> tuple[str, list[TicketSection]]:
    text = description or ""
    lines = text.splitlines(keepends=True)
    preamble: list[str] = []
    sections: list[TicketSection] = []
    current_heading: str | None = None
    current_title = ""
    current_body: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal current_heading, current_title, current_body
        if current_heading is None:
            return
        sections.append(
            TicketSection(
                heading=current_heading,
                title=current_title,
                normalized_title=_normalize_title(current_title),
                body="".join(current_body),
                index=len(sections),
            )
        )
        current_heading = None
        current_title = ""
        current_body = []

    for line in lines:
        heading = None if in_fence else _SECTION_HEADING_RE.match(line)
        if heading is not None:
            flush()
            current_heading = line
            current_title = heading.group("title").strip().strip("#").strip()
            continue
        if current_heading is None:
            preamble.append(line)
        else:
            current_body.append(line)
        if _FENCE_RE.match(line):
            in_fence = not in_fence
    flush()
    return "".join(preamble), sections


def _issue_description(issue: Any) -> str:
    if isinstance(issue, dict):
        return str(issue.get("description") or "")
    return str(getattr(issue, "description", "") or "")


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _clean_preamble(text: str) -> str:
    kept = [
        line
        for line in (text or "").splitlines()
        if not _SCAFFOLD_LINE_RE.match(line.strip())
    ]
    return "\n".join(kept).strip()


def _select_latest_matching(
    selected: dict[int, TicketSection],
    sections: list[TicketSection],
    names: set[str],
) -> None:
    latest_by_name: dict[str, TicketSection] = {}
    for section in sections:
        if section.normalized_title in names:
            latest_by_name[section.normalized_title] = section
    for section in latest_by_name.values():
        selected[section.index] = section


def _latest_section(
    sections: list[TicketSection],
    names: set[str],
) -> TicketSection | None:
    for section in reversed(sections):
        if section.normalized_title in names:
            return section
    return None


def _leading_scope_sections(sections: list[TicketSection]) -> list[TicketSection]:
    out: list[TicketSection] = []
    for section in sections:
        if section.normalized_title in _AGENT_OWNED_SECTIONS:
            break
        out.append(section)
    return out


def _plan_has_unresolved_work(body: str) -> bool:
    lowered = body.lower()
    return "- [ ]" in body or any(
        marker in lowered
        for marker in (
            "unresolved",
            "remaining",
            "pending",
            "todo",
            "not done",
        )
    )
