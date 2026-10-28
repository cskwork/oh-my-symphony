"""Semantic planning identities, Idea readiness, and cross-document references."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from .models import (
    DocumentView, OwnedDecision, PlanningSection, PmError, PmFinding,
    SemanticItem, SemanticKind, StoryboardFrame, WbsTask,
)

_MARKER = re.compile(
    r"\s*<!--\s*symphony:(req|scenario|storyboard|contract|decision)="
    r"([A-Z]+-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\s*-->\s*"
)
_PREFIX: dict[SemanticKind, str] = {
    "requirement": "REQ",
    "scenario": "SC",
    "storyboard": "SB",
    "contract": "API",
    "decision": "DEC",
}
_MARKER_KIND: dict[str, SemanticKind] = {"req": "requirement", "scenario": "scenario", "storyboard": "storyboard", "contract": "contract", "decision": "decision"}
_HEADING = {
    "requirement": re.compile(r"^#{2,6}\s+Requirement:\s*(.+?)\s*$", re.I),
    "scenario": re.compile(r"^#{2,6}\s+Scenario:\s*(.+?)\s*$", re.I),
    "storyboard": re.compile(r"^#{2,6}\s+Storyboard:\s*(.+?)\s*$", re.I),
    "contract": re.compile(r"^#{2,6}\s+(?:API\s+)?Contract:\s*(.+?)\s*$", re.I),
    "decision": re.compile(r"^#{2,6}\s+Decision:\s*(.+?)\s*$", re.I),
}
_IDEA_SECTIONS = {
    "problem": ("problem",),
    "users": ("intended users", "users"),
    "outcome": ("desired outcome", "outcome"),
    "scope": ("scope",),
    "non_goals": ("non-goals", "non goals"),
    "constraints": ("constraints",),
    "success_criteria": ("success criteria",),
    "assumptions": ("assumptions",),
    "open_decisions": ("open decisions",),
    "affected_capabilities": ("affected capabilities",),
}


@dataclass(frozen=True)
class SemanticIndex:
    items: tuple[SemanticItem, ...]
    findings: tuple[PmFinding, ...]

    @property
    def by_id(self) -> dict[str, SemanticItem]:
        return {item.semantic_id: item for item in self.items}


@dataclass(frozen=True)
class IdeaSemantics:
    decisions: tuple[OwnedDecision, ...]
    affected_capabilities: tuple[str, ...]
    findings: tuple[PmFinding, ...]


def mint_semantic_id(kind: SemanticKind, reserved: Iterable[str] = ()) -> str:
    prefix = _PREFIX[kind]
    occupied = set(reserved)
    while True:
        candidate = f"{prefix}-{secrets.token_hex(4).upper()}"
        if candidate not in occupied:
            return candidate


def build_semantic_index(
    documents: Sequence[DocumentView], *, retired_ids: Iterable[str] = ()
) -> SemanticIndex:
    retired = set(retired_ids)
    items: list[SemanticItem] = []
    findings: list[PmFinding] = []
    seen: dict[str, SemanticItem] = {}
    for document in documents:
        if document.document_id == "tasks":
            continue
        for item in semantic_items(
            document.source, document.document_id, document.path
        ):
            prior = seen.get(item.semantic_id)
            if prior is not None:
                findings.append(PmFinding(
                    "duplicate_semantic_id",
                    f"{item.semantic_id} is defined more than once",
                    severity="blocking",
                    document_id=item.document_id,
                    item_id=item.semantic_id,
                    field="semantic_id",
                    path=(prior.document_id, item.document_id),
                ))
            else:
                seen[item.semantic_id] = item
            if item.semantic_id in retired:
                findings.append(PmFinding(
                    "retired_semantic_id_reused",
                    f"retired identity {item.semantic_id} cannot be reused",
                    severity="blocking",
                    document_id=item.document_id,
                    item_id=item.semantic_id,
                    field="semantic_id",
                ))
            expected_prefix = _PREFIX[item.kind] + "-"
            if not item.semantic_id.startswith(expected_prefix):
                findings.append(PmFinding(
                    "semantic_id_kind_mismatch",
                    f"{item.semantic_id} cannot identify a {item.kind}",
                    severity="blocking", document_id=item.document_id,
                    item_id=item.semantic_id, field="semantic_id",
                ))
            if not _kind_allowed_in_document(item.kind, item.document_id):
                findings.append(PmFinding(
                    "semantic_kind_wrong_document",
                    f"{item.kind} identity {item.semantic_id} is not allowed in {item.document_id}",
                    severity="blocking", document_id=item.document_id,
                    item_id=item.semantic_id, field="semantic_id",
                ))
            items.append(item)
    requirements = [item for item in items if item.kind == "requirement"]
    scenarios = [item for item in items if item.kind == "scenario"]
    for requirement in requirements:
        next_requirement_line = min(
            (item.line for item in requirements if item.document_id == requirement.document_id and item.line > requirement.line),
            default=10**9,
        )
        if not any(
            scenario.document_id == requirement.document_id
            and requirement.line < scenario.line < next_requirement_line
            for scenario in scenarios
        ):
            findings.append(PmFinding(
                "requirement_scenario_required",
                f"requirement {requirement.semantic_id} needs a typed scenario",
                severity="blocking", document_id=requirement.document_id,
                item_id=requirement.semantic_id, field="scenarios",
            ))
    return SemanticIndex(tuple(items), tuple(findings))


def semantic_items(source: str, document_id: str, path: str) -> tuple[SemanticItem, ...]:
    lines = source.splitlines()
    excluded = _excluded_lines(lines)
    headings: dict[str, tuple[str, int]] = {}
    items: list[SemanticItem] = []
    for index, line in enumerate(lines):
        if excluded[index]:
            continue
        for kind, pattern in _HEADING.items():
            match = pattern.match(line.strip())
            if match:
                headings[kind] = (match.group(1).strip(), index + 1)
        marker = _MARKER.fullmatch(line)
        if marker is None:
            continue
        kind = _MARKER_KIND[marker.group(1)]
        semantic_id = marker.group(2)
        title, heading_line = headings.get(kind, (semantic_id, index + 1))
        items.append(SemanticItem(
            semantic_id, kind, title, document_id, path, heading_line
        ))
    return tuple(items)


_STORYBOARD_FIELD = re.compile(r"^\s*-\s*([^:]+):\s*(.*)$")
_SECTION_HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


def storyboard_frames(source: str, document_id: str = "ux") -> tuple[StoryboardFrame, ...]:
    """Project storyboard blocks without making the browser parse Markdown."""
    lines = source.splitlines()
    excluded = _excluded_lines(lines)
    items = [item for item in semantic_items(source, document_id, "ux.md") if item.kind == "storyboard"]
    frames: list[StoryboardFrame] = []
    for order, item in enumerate(items):
        start = max(0, item.line - 1)
        end = min(
            (other.line - 1 for other in items if other.line > item.line),
            default=len(lines),
        )
        values: dict[str, str] = {}
        for index in range(start, end):
            if excluded[index]:
                continue
            match = _STORYBOARD_FIELD.match(lines[index])
            if match:
                values[match.group(1).strip().casefold()] = match.group(2).strip()
        legacy_ui = values.get("ui/action/response")
        legacy_outcome = values.get("success/alternate/failure")
        frames.append(StoryboardFrame(
            storyboard_id=item.semantic_id,
            title=item.title,
            order=order,
            document_id=document_id,
            screen_route=values.get("screen/route", ""),
            actor_goal=values.get("actor/goal", ""),
            initial_state=values.get("initial state", ""),
            ui=values.get("ui", legacy_ui or ""),
            user_action=values.get("user action", ""),
            system_response=values.get("system response", ""),
            success=values.get("success", legacy_outcome or ""),
            alternate=values.get("alternate", ""),
            failure=values.get("failure", ""),
            requirement_refs=_refs(values.get("requirement references", "")),
            design_refs=_refs(values.get("design references", "")),
            legacy_combined=legacy_ui is not None or legacy_outcome is not None,
        ))
    return tuple(frames)


def planning_sections(documents: Sequence[DocumentView]) -> tuple[PlanningSection, ...]:
    sections: list[PlanningSection] = []
    for document in documents:
        excluded = _excluded_lines(document.source.splitlines())
        for index, line in enumerate(document.source.splitlines()):
            if excluded[index]:
                continue
            match = _SECTION_HEADING.match(line.strip())
            if not match:
                continue
            label = match.group(2).strip()
            slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or f"section-{index + 1}"
            sections.append(PlanningSection(f"{document.document_id}:{slug}:{index + 1}", document.document_id, label))
    return tuple(sections)


def set_storyboard_fields(source: str, storyboard_id: str, fields: dict[str, object]) -> str:
    allowed = {
        "title", "screen_route", "actor_goal", "initial_state", "ui",
        "user_action", "system_response", "success", "alternate", "failure",
        "requirement_refs", "design_refs",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise PmError("invalid_request", "unknown storyboard field", details={"fields": sorted(unknown)})
    frames = storyboard_frames(source)
    matching = [frame for frame in frames if frame.storyboard_id == storyboard_id]
    if len(matching) != 1:
        raise PmError("storyboard_not_found", "storyboard identity is missing or ambiguous", status=409)
    current = matching[0]
    interaction = {"ui", "user_action", "system_response"}
    outcomes = {"success", "alternate", "failure"}
    if current.legacy_combined:
        for family in (interaction, outcomes):
            if set(fields) & family and not family <= set(fields):
                raise PmError("legacy_storyboard_requires_complete_fields", "legacy combined storyboard fields must be replaced together", status=409)
    lines = source.splitlines(keepends=True)
    marker = re.compile(rf"\s*<!--\s*symphony:storyboard={re.escape(storyboard_id)}\s*-->\s*")
    marker_indexes = [index for index, line in enumerate(lines) if marker.fullmatch(line.rstrip("\r\n"))]
    if len(marker_indexes) != 1:
        raise PmError("storyboard_not_found", "storyboard identity is missing or ambiguous", status=409)
    marker_index = marker_indexes[0]
    heading_index = marker_index - 1
    while heading_index >= 0 and not _HEADING["storyboard"].match(lines[heading_index].strip()):
        heading_index -= 1
    if heading_index < 0:
        raise PmError("unsupported_format", "storyboard heading is missing", status=409)
    next_index = len(lines)
    for index in range(marker_index + 1, len(lines)):
        if _HEADING["storyboard"].match(lines[index].strip()):
            next_index = index
            break
    newline = _dominant_newline(source)
    title = fields.get("title", current.title)
    if not isinstance(title, str):
        raise PmError("invalid_request", "storyboard title must be text")
    title = title.strip()
    if not title or len(title.splitlines()) != 1:
        raise PmError("invalid_request", "storyboard title must be non-empty single-line text")
    lines[heading_index] = re.sub(
        r"(Storyboard:\s*).*?(?:\r?\n)?$",
        lambda match: f"{match.group(1)}{title}{newline}",
        lines[heading_index], flags=re.I,
    )
    key_names = {
        "screen_route": "Screen/route", "actor_goal": "Actor/goal",
        "initial_state": "Initial state", "ui": "UI", "user_action": "User action",
        "system_response": "System response", "success": "Success",
        "alternate": "Alternate", "failure": "Failure",
        "requirement_refs": "Requirement references", "design_refs": "Design references",
    }
    body = lines[marker_index + 1:next_index]
    existing: dict[str, int] = {}
    for offset, line in enumerate(body):
        match = _STORYBOARD_FIELD.match(line.rstrip("\r\n"))
        if match:
            existing[match.group(1).strip().casefold()] = offset
    if current.legacy_combined and set(fields) & interaction:
        body = [line for line in body if not line.lstrip().casefold().startswith("- ui/action/response:")]
        existing = {}
    if current.legacy_combined and set(fields) & outcomes:
        body = [line for line in body if not line.lstrip().casefold().startswith("- success/alternate/failure:")]
        existing = {}
    for field, label in key_names.items():
        if field not in fields:
            continue
        raw = fields[field]
        if field.endswith("_refs"):
            if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
                raise PmError("invalid_request", f"{field} must be a string list")
            value = ", ".join(raw)
        elif not isinstance(raw, str):
            raise PmError("invalid_request", f"{field} must be text")
        else:
            value = raw.strip()
        line = f"- {label}: {value}{newline}"
        found = next((i for i, existing_line in enumerate(body) if existing_line.lstrip().casefold().startswith(f"- {label.casefold()}:") ), None)
        if found is None:
            body.append(line)
        else:
            body[found] = line
    lines[marker_index + 1:next_index] = body
    return "".join(lines)


def _refs(value: str) -> tuple[str, ...]:
    return tuple(item for item in re.split(r"[\s,\[\]]+", value) if item)


def _dominant_newline(source: str) -> str:
    crlf = source.count("\r\n")
    lf = source.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def validate_idea(source: str, index: SemanticIndex) -> IdeaSemantics:
    sections = _sections(source)
    findings: list[PmFinding] = []
    for field, aliases in _IDEA_SECTIONS.items():
        value = next((sections[name] for name in aliases if name in sections), "")
        if not _meaningful(value):
            findings.append(PmFinding(
                "idea_field_required",
                f"Idea field {aliases[0]} is required",
                severity="blocking", document_id="proposal", field=field,
            ))

    decision_items = [item for item in index.items if item.kind == "decision" and item.document_id == "proposal"]
    decisions: list[OwnedDecision] = []
    for item in decision_items:
        body = _body_after_line(source, item.line)
        values, duplicate_keys = _key_values(body)
        for field in sorted(duplicate_keys):
            findings.append(_decision_finding(
                item,
                "decision_metadata_ambiguous",
                f"Decision {item.semantic_id} defines {field} more than once",
                field,
            ))
        owner = values.get("owner", "").strip()
        impact = values.get("impact", "").strip().lower()
        status = values.get("status", "").strip().lower()
        reason = values.get("reason", "").strip()
        if not owner:
            findings.append(_decision_finding(item, "decision_owner_required", "Decision owner is required", "owner"))
        if impact not in {"low", "medium", "high"}:
            findings.append(_decision_finding(item, "decision_impact_invalid", "Decision impact must be Low, Medium, or High", "impact"))
        if status not in {"unresolved", "accepted"}:
            findings.append(_decision_finding(item, "decision_status_invalid", "Decision status must be Unresolved or Accepted", "status"))
        if impact == "high" and status == "unresolved":
            findings.append(_decision_finding(item, "blocking_decision", f"High-impact decision {item.semantic_id} is unresolved", "status"))
        if impact == "low" and status == "accepted" and not reason:
            findings.append(_decision_finding(item, "decision_reason_required", f"Accepted low-impact decision {item.semantic_id} requires a reason", "reason"))
        if owner and impact in {"low", "medium", "high"} and status in {"unresolved", "accepted"}:
            typed_impact: Literal["low", "medium", "high"]
            if impact == "low":
                typed_impact = "low"
            elif impact == "medium":
                typed_impact = "medium"
            else:
                typed_impact = "high"
            typed_status: Literal["unresolved", "accepted"] = (
                "unresolved" if status == "unresolved" else "accepted"
            )
            decisions.append(OwnedDecision(
                item.semantic_id, item.title, owner, typed_impact, typed_status, reason
            ))

    open_decisions = sections.get("open decisions", "")
    if _meaningful(open_decisions) and not decision_items and not _explicitly_no_open_decisions(open_decisions):
        findings.append(PmFinding(
            "owned_decision_required",
            "Open decisions must use a server-owned decision marker",
            severity="blocking", document_id="proposal", field="open_decisions",
        ))
    capabilities = tuple(_list_values(sections.get("affected capabilities", "")))
    return IdeaSemantics(tuple(decisions), capabilities, tuple(findings))


def validate_task_references(tasks: Sequence[WbsTask], index: SemanticIndex) -> tuple[PmFinding, ...]:
    by_id = index.by_id
    findings: list[PmFinding] = []
    families = (
        ("requirement_refs", {"requirement", "scenario"}),
        ("storyboard_refs", {"storyboard"}),
        ("design_refs", {"contract"}),
    )
    for task in tasks:
        for field, expected_kinds in families:
            for reference in getattr(task, field):
                target = by_id.get(reference)
                if target is None or target.kind not in expected_kinds:
                    findings.append(PmFinding(
                        "unresolved_reference",
                        f"task {task.task_id} {field} references unknown {'/'.join(sorted(expected_kinds))} {reference}",
                        severity="blocking", document_id="tasks", item_id=task.task_id,
                        field=field, path=(reference,),
                    ))
    return tuple(findings)


def append_semantic_block(source: str, kind: SemanticKind, semantic_id: str, title: str) -> str:
    safe_title = title.strip() or f"New {kind}"
    if kind == "requirement":
        block = f"### Requirement: {safe_title}\n<!-- symphony:req={semantic_id} -->\n"
    elif kind == "scenario":
        block = f"#### Scenario: {safe_title}\n<!-- symphony:scenario={semantic_id} -->\n- GIVEN\n- WHEN\n- THEN\n"
    elif kind == "storyboard":
        block = f"### Storyboard: {safe_title}\n<!-- symphony:storyboard={semantic_id} -->\n- Screen/route:\n- Actor/goal:\n- Initial state:\n- UI/action/response:\n- Success/alternate/failure:\n"
    elif kind == "contract":
        block = f"### Contract: {safe_title}\n<!-- symphony:contract={semantic_id} -->\n- Owner/boundary:\n- Auth/schema/errors/version/idempotency:\n- Retry/partial failure:\n- Verification:\n"
    elif kind == "decision":
        block = f"### Decision: {safe_title}\n<!-- symphony:decision={semantic_id} -->\n- Owner:\n- Impact:\n- Status:\n- Reason:\n"
    else:
        raise ValueError(kind)
    return source.rstrip() + "\n\n" + block


def _decision_finding(item: SemanticItem, code: str, message: str, field: str) -> PmFinding:
    return PmFinding(code, message, severity="blocking", document_id="proposal", item_id=item.semantic_id, field=field)


def _kind_allowed_in_document(kind: str, document_id: str) -> bool:
    if kind in {"requirement", "scenario"}:
        return document_id.startswith("spec:")
    return {
        "decision": "proposal",
        "storyboard": "ux",
        "contract": "design",
    }.get(kind) == document_id


def _sections(source: str) -> dict[str, str]:
    lines = source.splitlines()
    excluded = _excluded_lines(lines)
    result: dict[str, list[str]] = {}
    current: str | None = None
    for index, line in enumerate(lines):
        if excluded[index]:
            if current is not None:
                result[current].append(line)
            continue
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            section = match.group(1).strip().lower()
            result.setdefault(section, [])
            current = section
        elif current is not None:
            result[current].append(line)
    return {key: "\n".join(value).strip() for key, value in result.items()}


def _body_after_line(source: str, heading_line: int) -> str:
    lines = source.splitlines()
    excluded = _excluded_lines(lines)
    start = heading_line
    collected: list[str] = []
    for index, line in enumerate(lines[start:], start=start):
        if collected and not excluded[index] and re.match(r"^#{2,6}\s+", line):
            break
        collected.append(line)
    return "\n".join(collected)


def _key_values(body: str) -> tuple[dict[str, str], set[str]]:
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    lines = body.splitlines()
    excluded = _excluded_lines(lines)
    controlled = {"owner", "impact", "status", "reason"}
    for index, line in enumerate(lines):
        if excluded[index]:
            continue
        match = re.match(r"^\s*[-*]\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            key = match.group(1).strip().lower()
            if key not in controlled:
                continue
            if key in result:
                duplicates.add(key)
                continue
            result[key] = match.group(2).strip()
    return result, duplicates


def _explicitly_no_open_decisions(body: str) -> bool:
    lines = body.splitlines()
    excluded = _excluded_lines(lines)
    actual = " ".join(
        line.strip() for index, line in enumerate(lines)
        if not excluded[index] and line.strip() and not line.lstrip().startswith("<!--")
    )
    normalized = re.sub(r"[.。!！]+$", "", actual).strip().lower()
    return normalized in {
        "none", "no open decisions", "no unresolved decisions", "all settled"
    }


def _list_values(body: str) -> list[str]:
    values: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if match and ":" not in match.group(1):
            values.append(match.group(1).strip())
    if not values and _meaningful(body):
        values = [line.strip() for line in body.splitlines() if line.strip()]
    return values


def _meaningful(body: str) -> bool:
    return any(
        line.strip() and not line.lstrip().startswith("<!--")
        for line in body.splitlines()
    )


def _excluded_lines(lines: Sequence[str]) -> list[bool]:
    excluded: list[bool] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker is not None:
            delimiter = marker.group(1)
            token = delimiter[0]
            if fence is None:
                fence = (token, len(delimiter))
            elif fence[0] == token and len(delimiter) >= fence[1]:
                fence = None
            excluded.append(True)
            continue
        excluded.append(fence is not None or stripped.startswith(">") or line.startswith("    ") or line.startswith("\t"))
    return excluded
