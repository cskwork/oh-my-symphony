"""Managed, human-readable WBS grammar and graph validation."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import yaml

from .models import (
    PmError, PmFinding, VerificationProcedureSelection, WbsDependency, WbsTask,
)
from .yaml_safe import load_unique_yaml

FORMAT_MARKER = "<!-- symphony-wbs-format: 1 -->"
_BLOCK_RE = re.compile(
    r"<!-- symphony-(?P<kind>group|task):(?P<id>[A-Z]-[A-Z0-9]+) -->"
    r"(?P<body>.*?)"
    r"<!-- /symphony-(?P=kind) -->",
    re.DOTALL,
)
_FENCE = re.escape(chr(96) * 3)
_YAML_RE = re.compile(
    _FENCE + r"yaml symphony-(?:group|task)\n(?P<yaml>.*?)\n" + _FENCE,
    re.DOTALL,
)
_TASK_LINE_RE = re.compile(r"(?m)^(?P<prefix>\s*[-*+]\s+\[[ xX]\]\s+)(?P<text>[^\r\n]+)\r?$")
_CANCELLED_TASK_LINE_RE = re.compile(r"(?m)^\s*[-*+]\s+Cancelled:\s+(?P<text>[^\r\n]+)\r?$")
_HEADING_RE = re.compile(r"(?m)^\s*##+\s+(?P<text>[^\r\n]+)\r?$")
_CHECKBOX_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]\s*(?P<text>.*)$")
_ID_RE = re.compile(r"^[TG]-[A-Z0-9]{8,64}$")
_SCALAR_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[a-z_]+):(?P<rest>.*)$")
_MANAGED_MARKER_RE = re.compile(
    r"<!--\s*(?P<close>/)?symphony-(?P<kind>[a-z_-]+)(?::(?P<id>[^\s>]+))?\s*-->"
)
_SECTION_RE = re.compile(r"(?m)^###\s+(?P<title>[^\n]+)\s*$")


@dataclass(frozen=True)
class ManagedBlock:
    kind: str
    item_id: str
    start: int
    end: int
    body_start: int
    body_end: int
    yaml_start: int
    yaml_end: int
    metadata: dict[str, Any]
    source: str


@dataclass(frozen=True)
class ParsedWbs:
    source: str
    blocks: tuple[ManagedBlock, ...]
    tasks: tuple[WbsTask, ...]
    findings: tuple[PmFinding, ...]

    def serialize(self) -> str:
        """Unchanged documents are byte-identical."""
        return self.source


def parse_wbs(source: str) -> ParsedWbs:
    findings: list[PmFinding] = []
    blocks: list[ManagedBlock] = []
    tasks: list[WbsTask] = []
    fenced = _fenced_spans(source)
    fence_starts = tuple(start for start, _ in fenced)
    format_positions = [
        match.start() for match in re.finditer(re.escape(FORMAT_MARKER), source)
    ]
    if len(format_positions) != 1 or _inside_span(
        fenced, fence_starts, format_positions[0]
    ):
        findings.append(
            PmFinding(
                "unsupported_wbs_format",
                "managed WBS format marker is missing",
                severity="blocking",
                document_id="tasks",
            )
        )
        return ParsedWbs(source, (), (), tuple(findings))

    managed_markers = list(_MANAGED_MARKER_RE.finditer(source))
    recognized: list[re.Match[str]] = []
    scan_depth = 0
    for marker in managed_markers:
        if marker.group("kind") == "wbs-format":
            continue
        inside_fence = _inside_span(fenced, fence_starts, marker.start())
        if inside_fence:
            findings.append(
                PmFinding(
                    "managed_marker_in_fence",
                    "managed WBS markers cannot appear inside fenced code",
                    severity="blocking",
                    document_id="tasks",
                )
            )
            continue
        if marker.group("kind") not in {"task", "group"}:
            findings.append(
                PmFinding(
                    "unknown_marker_kind",
                    f"unknown managed marker kind {marker.group('kind')}",
                    severity="blocking",
                    document_id="tasks",
                )
            )
            continue
        if not marker.group("close") and not marker.group("id"):
            findings.append(
                PmFinding(
                    "invalid_marker",
                    "managed WBS opening marker requires an id",
                    severity="blocking",
                    document_id="tasks",
                )
            )
            continue
        if not marker.group("close") and scan_depth:
            findings.append(
                PmFinding(
                    "nested_managed_block",
                    "managed WBS blocks cannot be nested; use parent_id for hierarchy",
                    severity="blocking",
                    document_id="tasks",
                )
            )
        scan_depth = max(0, scan_depth - 1) if marker.group("close") else scan_depth + 1
        recognized.append(marker)
    marker_stack: list[str] = []
    for marker in recognized:
        kind = marker.group("kind")
        if marker.group("close"):
            if not marker_stack or marker_stack[-1] != kind:
                findings.append(
                    PmFinding(
                        "unbalanced_marker",
                        "managed WBS closing marker is unmatched",
                        severity="blocking",
                        document_id="tasks",
                    )
                )
            else:
                marker_stack.pop()
        else:
            marker_stack.append(kind)
    if marker_stack:
        findings.append(
            PmFinding(
                "unbalanced_marker",
                "managed WBS marker is unterminated",
                severity="blocking",
                document_id="tasks",
            )
        )

    marker_ids: set[str] = set()
    for match in _BLOCK_RE.finditer(source):
        if _inside_span(fenced, fence_starts, match.start()):
            continue
        kind = match.group("kind")
        item_id = match.group("id")
        body = match.group("body")
        yaml_match = _YAML_RE.search(body)
        if item_id in marker_ids:
            findings.append(
                PmFinding(
                    "duplicate_id",
                    f"duplicate WBS id {item_id}",
                    severity="blocking",
                    item_id=item_id,
                )
            )
            continue
        marker_ids.add(item_id)
        if yaml_match is None:
            findings.append(
                PmFinding(
                    "missing_metadata",
                    f"{item_id} has no managed YAML block",
                    severity="blocking",
                    item_id=item_id,
                )
            )
            continue
        try:
            metadata = load_unique_yaml(yaml_match.group("yaml")) or {}
        except yaml.YAMLError as exc:
            findings.append(
                PmFinding(
                    "malformed_metadata",
                    f"{item_id} metadata is invalid: {exc}",
                    severity="blocking",
                    item_id=item_id,
                )
            )
            continue
        if not isinstance(metadata, dict) or metadata.get("id") != item_id:
            findings.append(
                PmFinding(
                    "identity_mismatch",
                    f"{item_id} marker and metadata id differ",
                    severity="blocking",
                    item_id=item_id,
                )
            )
            continue
        shape_errors = _metadata_findings(kind, item_id, metadata)
        if shape_errors:
            findings.extend(shape_errors)
            continue
        absolute_body = match.start("body")
        block = ManagedBlock(
            kind=kind,
            item_id=item_id,
            start=match.start(),
            end=match.end(),
            body_start=match.start("body"),
            body_end=match.end("body"),
            yaml_start=absolute_body + yaml_match.start("yaml"),
            yaml_end=absolute_body + yaml_match.end("yaml"),
            metadata=dict(metadata),
            source=match.group(0),
        )
        blocks.append(block)
        task = _task_from_block(block, body, findings)
        if task is not None:
            tasks.append(task)

    task_line_spans: list[tuple[int, int]] = []
    for block in blocks:
        if block.kind != "task":
            continue
        line = _TASK_LINE_RE.search(source, block.body_start, block.body_end)
        if line is not None:
            task_line_spans.append((line.start(), line.end()))
    # Blocks are matched left to right without overlap, so these spans are
    # sorted and disjoint; bisect keeps 1,000-task documents linear.
    task_lines = tuple(task_line_spans)
    task_line_starts = tuple(start for start, _ in task_lines)
    for checkbox in _CHECKBOX_RE.finditer(source):
        if _inside_span(task_lines, task_line_starts, checkbox.start()):
            continue
        reason = (
            "checkbox inside fenced code"
            if _inside_span(fenced, fence_starts, checkbox.start())
            else "checkbox outside executable task marker"
        )
        findings.append(
            PmFinding(
                "unsafe_checkbox",
                reason,
                severity="blocking",
                document_id="tasks",
                path=(str(source.count("\n", 0, checkbox.start()) + 1),),
            )
        )
    findings.extend(validate_wbs(tuple(tasks)))
    return ParsedWbs(source, tuple(blocks), tuple(tasks), tuple(findings))


def patch_task_field(source: str, task_id: str, field: str, value: Any) -> str:
    """Patch one known task field without rewriting unrelated source."""
    parsed = parse_wbs(source)
    blocking = [item for item in parsed.findings if item.severity == "blocking"]
    if blocking:
        raise PmError(
            "unsupported_format",
            "tasks.md is not safe for structured editing",
            status=409,
            details={"findings": [item.code for item in blocking]},
        )
    block = next((item for item in parsed.blocks if item.item_id == task_id), None)
    if block is None:
        raise PmError("unknown_task", f"unknown task {task_id}", status=404)
    return _patch_known_task_field(source, block, field, value)


def _patch_known_task_field(
    source: str, block: ManagedBlock, field: str, value: Any
) -> str:
    if field == "title":
        body = source[block.body_start : block.body_end]
        match = (
            _HEADING_RE.search(body)
            if block.kind == "group" else _TASK_LINE_RE.search(body)
        )
        if match is None or not isinstance(value, str) or not value.strip():
            raise PmError("invalid_field", "task title must be non-empty")
        start = block.body_start + match.start("text")
        end = block.body_start + match.end("text")
        return source[:start] + value.strip() + source[end:]
    if block.kind == "group" and field not in {"parent_id", "order"}:
        raise PmError(
            "invalid_field", f"field {field!r} is not editable on a group"
        )
    section_names = {
        "description": "Scope",
        "acceptance": "Acceptance criteria",
        "verification": "Verification",
        "integration_impact": "Integration impact",
        "risk": "Risk",
        "notes": "Notes",
    }
    if field in section_names:
        body = source[block.body_start : block.body_end]
        rendered_value = _render_section_value(field, value)
        patched = _patch_section(body, section_names[field], rendered_value)
        return source[: block.body_start] + patched + source[block.body_end :]
    if field not in {
        "parent_id",
        "order",
        "type",
        "priority",
        "human_owner",
        "agent_kind",
        "depends_on",
        "requirements",
        "storyboards",
        "design_refs",
        "target_date",
        "estimate",
        "cancelled",
        "cancellation_reason",
        "successors",
    }:
        raise PmError("invalid_field", f"field {field!r} is not structured-editable")
    yaml_text = source[block.yaml_start : block.yaml_end]
    replacement = _patch_yaml_field(yaml_text, field, value)
    return source[: block.yaml_start] + replacement + source[block.yaml_end :]


def patch_task_fields(
    source: str,
    changes: list[tuple[str, str, Any]],
    *,
    parsed: ParsedWbs | None = None,
) -> str:
    """Patch many task fields with one full-document parse.

    Each affected managed block is then patched in isolation, so source spans
    outside that block remain byte-for-byte stable and repeated fields on one
    task preserve request order. A caller that already parsed ``source`` may
    pass that result to avoid a second full parse; it must describe ``source``.
    """
    if not changes:
        return source
    if parsed is None or parsed.source != source:
        parsed = parse_wbs(source)
    blocking = [item for item in parsed.findings if item.severity == "blocking"]
    if blocking:
        raise PmError(
            "unsupported_format", "tasks.md is not safe for structured editing",
            status=409, details={"findings": [item.code for item in blocking]},
        )
    by_id = {block.item_id: block for block in parsed.blocks}
    grouped: dict[str, list[tuple[str, Any]]] = {}
    for task_id, field, value in changes:
        if task_id not in by_id:
            raise PmError("unknown_task", f"unknown task {task_id}", status=404)
        grouped.setdefault(task_id, []).append((field, value))
    prefix = f"{FORMAT_MARKER}\n"
    for task_id in sorted(grouped, key=lambda item: by_id[item].start, reverse=True):
        block = by_id[task_id]
        replacement = prefix + block.source
        for field, value in grouped[task_id]:
            local = next(
                item for item in parse_wbs(replacement).blocks
                if item.item_id == task_id
            )
            replacement = _patch_known_task_field(replacement, local, field, value)
        replacement = replacement[len(prefix):]
        source = source[:block.start] + replacement + source[block.end:]
    return source


def _render_section_value(field: str, value: Any) -> str:
    if field in {"acceptance", "verification"}:
        values = value if isinstance(value, list) else str(value or "").splitlines()
        cleaned = [str(item).strip().removeprefix("- ").strip() for item in values if str(item).strip()]
        return "\n".join(f"- {item}" for item in cleaned)
    return str(value or "").strip()


def _patch_section(body: str, title: str, value: str) -> str:
    matches = list(_SECTION_RE.finditer(body))
    target = next((match for match in matches if match.group("title").strip().casefold() == title.casefold()), None)
    replacement = f"### {title}\n{value}\n"
    if target is None:
        suffix = "" if body.endswith("\n") else "\n"
        return body + suffix + replacement
    index = matches.index(target)
    end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
    return body[: target.start()] + replacement + body[end:]


def validate_wbs(tasks: tuple[WbsTask, ...]) -> tuple[PmFinding, ...]:
    findings: list[PmFinding] = []
    by_id = {task.task_id: task for task in tasks}
    if len(by_id) != len(tasks):
        findings.append(
            PmFinding("duplicate_id", "WBS contains duplicate IDs", severity="blocking")
        )
    for task in tasks:
        if not _ID_RE.match(task.task_id):
            findings.append(PmFinding("invalid_id", f"invalid permanent id {task.task_id}", severity="blocking", item_id=task.task_id))
        if task.parent_id is not None:
            parent = by_id.get(task.parent_id)
            if parent is None:
                findings.append(PmFinding("missing_parent", f"{task.task_id} parent {task.parent_id} does not exist", severity="blocking", item_id=task.task_id))
            elif parent.executable:
                findings.append(PmFinding("invalid_parent", f"{task.task_id} parent {task.parent_id} is executable", severity="blocking", item_id=task.task_id))
        if task.cancelled:
            if not task.cancellation_reason:
                findings.append(PmFinding("cancellation_reason_required", f"{task.task_id} cancellation requires a reason", severity="blocking", item_id=task.task_id))
            for successor_id in task.successor_refs:
                successor = by_id.get(successor_id)
                if successor is None or not successor.executable or successor_id == task.task_id:
                    findings.append(PmFinding("invalid_cancellation_successor", f"{task.task_id} successor {successor_id} is not executable", severity="blocking", item_id=task.task_id))
        for dep in task.dependencies:
            if dep.task_id == task.task_id:
                findings.append(PmFinding("self_dependency", f"{task.task_id} depends on itself", severity="blocking", item_id=task.task_id))
            target = by_id.get(dep.task_id)
            if target is None:
                findings.append(PmFinding("dangling_dependency", f"{task.task_id} depends on missing {dep.task_id}", severity="blocking", item_id=task.task_id))
            elif target.cancelled:
                findings.append(PmFinding("cancelled_dependency", f"{task.task_id} still depends on cancelled task {dep.task_id}", severity="blocking", item_id=task.task_id))
            elif not target.executable:
                findings.append(PmFinding("group_dependency", f"{task.task_id} depends on group {dep.task_id}", severity="blocking", item_id=task.task_id))
            if dep.kind == "order_only" and not dep.justification:
                findings.append(PmFinding("missing_order_justification", f"{task.task_id} order-only dependency needs justification", severity="blocking", item_id=task.task_id))
    findings.extend(_cycle_findings(tasks, parent=False))
    findings.extend(_cycle_findings(tasks, parent=True))
    return tuple(findings)


def validate_wbs_limits(
    tasks: Sequence[WbsTask], *, max_nodes: int, max_edges: int,
    max_text_chars: int,
) -> tuple[PmFinding, ...]:
    """Validate configured planning limits against canonical task fields."""
    findings: list[PmFinding] = []
    if len(tasks) > max_nodes:
        findings.append(PmFinding(
            "node_limit_exceeded",
            f"plan has {len(tasks)} nodes; configured limit is {max_nodes}",
            severity="blocking", document_id="tasks", field="tasks",
        ))
    dependencies = sum(len(task.dependencies) for task in tasks)
    traceability = sum(
        len(task.requirement_refs) + len(task.storyboard_refs) + len(task.design_refs)
        for task in tasks
    )
    if dependencies > max_edges:
        findings.append(PmFinding(
            "edge_limit_exceeded",
            f"plan has {dependencies} dependency edges; configured limit is {max_edges}",
            severity="blocking", document_id="tasks", field="dependencies",
        ))
    if traceability > max_edges:
        findings.append(PmFinding(
            "edge_limit_exceeded",
            f"plan has {traceability} traceability edges; configured limit is {max_edges}",
            severity="blocking", document_id="tasks", field="traceability",
        ))
    text_fields = (
        ("title", lambda task: task.title),
        ("description", lambda task: task.description),
        ("human_owner", lambda task: task.human_owner or ""),
        ("agent_kind_override", lambda task: task.agent_kind_override or ""),
        ("priority", lambda task: task.priority or ""),
        ("target_date", lambda task: task.target_date or ""),
        ("estimate", lambda task: task.estimate or ""),
        ("acceptance", lambda task: "\n".join(task.acceptance)),
        ("verification", lambda task: "\n".join(task.verification)),
        ("dependency_justifications", lambda task: "\n".join(
            dependency.justification or "" for dependency in task.dependencies
        )),
        ("requirement_refs", lambda task: "\n".join(task.requirement_refs)),
        ("storyboard_refs", lambda task: "\n".join(task.storyboard_refs)),
        ("design_refs", lambda task: "\n".join(task.design_refs)),
        ("integration_impact", lambda task: task.integration_impact),
        ("risk", lambda task: task.risk),
        ("notes", lambda task: task.notes),
    )
    for task in tasks:
        for field, value in text_fields:
            if len(value(task)) > max_text_chars:
                findings.append(PmFinding(
                    "text_limit_exceeded",
                    f"task {task.task_id} {field} exceeds configured text limit {max_text_chars}",
                    severity="blocking", document_id="tasks",
                    item_id=task.task_id, field=field,
                ))
    return tuple(findings)


def _task_from_block(block: ManagedBlock, body: str, findings: list[PmFinding]) -> WbsTask | None:
    meta = block.metadata
    if block.kind == "group":
        title_match = _HEADING_RE.search(body)
        return WbsTask(
            task_id=block.item_id,
            parent_id=_string_or_none(meta.get("parent_id")),
            order=_integer(meta.get("order"), 0),
            kind="Group",
            title=title_match.group("text").strip() if title_match else block.item_id,
            executable=False,
        )
    cancelled = meta.get("cancelled") is True
    line = (
        _CANCELLED_TASK_LINE_RE.search(body)
        if cancelled else _TASK_LINE_RE.search(body)
    )
    if line is None or not line.group("text").strip():
        findings.append(PmFinding("empty_task", f"{block.item_id} has no executable checkbox text", severity="blocking", item_id=block.item_id))
        return None
    dependencies: list[WbsDependency] = []
    raw_deps = meta.get("depends_on") or []
    if isinstance(raw_deps, list):
        for item in raw_deps:
            if isinstance(item, str):
                dependencies.append(WbsDependency(item, "requires_code"))
            elif isinstance(item, dict) and isinstance(item.get("task_id"), str):
                kind = item.get("kind", "requires_code")
                if kind not in {"requires_code", "order_only"}:
                    kind = "requires_code"
                dependencies.append(WbsDependency(item["task_id"], kind, _string_or_none(item.get("justification"))))
    sections = _sections(body, block.item_id, findings)
    return WbsTask(
        task_id=block.item_id,
        parent_id=_string_or_none(meta.get("parent_id")),
        order=_integer(meta.get("order"), 0),
        kind=str(meta.get("type") or "Feature"),
        title=line.group("text").strip(),
        description=sections.get("scope", ""),
        priority=_string_or_none(meta.get("priority")),
        human_owner=_string_or_none(meta.get("human_owner")),
        agent_kind_override=_string_or_none(meta.get("agent_kind")),
        dependencies=tuple(dependencies),
        requirement_refs=_string_tuple(meta.get("requirements")),
        storyboard_refs=_string_tuple(meta.get("storyboards")),
        design_refs=_string_tuple(meta.get("design_refs")),
        acceptance=_section_bullets(sections.get("acceptance criteria", "")),
        verification=_section_bullets(sections.get("verification", "")),
        verification_policy=_verification_policy(meta),
        integration_impact=sections.get("integration impact", ""),
        risk=sections.get("risk", ""),
        notes=sections.get("notes", ""),
        target_date=_string_or_none(meta.get("target_date")),
        estimate=_string_or_none(meta.get("estimate")),
        cancelled=cancelled,
        cancellation_reason=_string_or_none(meta.get("cancellation_reason")),
        successor_refs=_string_tuple(meta.get("successors")),
        executable=not cancelled,
    )


def _patch_yaml_field(source: str, field: str, value: Any) -> str:
    lines = source.splitlines(keepends=True)
    start = None
    indent = ""
    for index, line in enumerate(lines):
        match = _SCALAR_LINE.match(line.rstrip("\r\n"))
        if match and match.group("key") == field:
            start = index
            indent = match.group("indent")
            break
    rendered = yaml.safe_dump({field: value}, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    rendered_lines = [f"{indent}{line}\n" for line in rendered.splitlines()]
    if start is None:
        suffix = "" if source.endswith("\n") else "\n"
        return source + suffix + "".join(rendered_lines).rstrip("\n")
    end = start + 1
    base_indent = len(indent)
    while end < len(lines):
        line = lines[end]
        current = len(line) - len(line.lstrip())
        if current <= base_indent and _SCALAR_LINE.match(line.rstrip("\r\n")):
            break
        end += 1
    preserved = [
        line
        for line in lines[start + 1 : end]
        if not line.strip() or line.lstrip().startswith("#")
    ]
    result = "".join(lines[:start] + rendered_lines + preserved + lines[end:])
    if not source.endswith("\n") and result.endswith("\n"):
        result = result[:-1]
    return result


def _cycle_findings(tasks: tuple[WbsTask, ...], *, parent: bool) -> list[PmFinding]:
    by_id = {task.task_id: task for task in tasks}
    graph: dict[str, tuple[str, ...]] = {}
    for task in tasks:
        graph[task.task_id] = ((task.parent_id,) if task.parent_id else ()) if parent else tuple(dep.task_id for dep in task.dependencies if dep.task_id in by_id)
    color: dict[str, int] = {}
    findings: list[PmFinding] = []
    for item_id in graph:
        if color.get(item_id, 0) != 0:
            continue
        path: list[str] = [item_id]
        positions = {item_id: 0}
        color[item_id] = 1
        frames: list[tuple[str, int]] = [(item_id, 0)]
        while frames:
            node, index = frames[-1]
            neighbors = graph.get(node, ())
            if index >= len(neighbors):
                frames.pop()
                color[node] = 2
                positions.pop(node, None)
                path.pop()
                continue
            next_node = neighbors[index]
            frames[-1] = (node, index + 1)
            if color.get(next_node, 0) == 0:
                color[next_node] = 1
                positions[next_node] = len(path)
                path.append(next_node)
                frames.append((next_node, 0))
            elif color.get(next_node) == 1:
                start = positions[next_node]
                cycle = tuple(path[start:] + [next_node])
                findings.append(PmFinding("parent_cycle" if parent else "dependency_cycle", " -> ".join(cycle), severity="blocking", item_id=node, path=cycle))
    return findings


def _fenced_spans(source: str) -> tuple[tuple[int, int], ...]:
    matches = list(re.finditer(r"(?m)^" + _FENCE + r".*$", source))
    spans = [
        (matches[index].start(), matches[index + 1].end())
        for index in range(0, len(matches) - 1, 2)
    ]
    if len(matches) % 2:
        spans.append((matches[-1].start(), len(source)))
    return tuple(spans)


def _inside_span(
    spans: tuple[tuple[int, int], ...], starts: tuple[int, ...], position: int
) -> bool:
    index = bisect_right(starts, position) - 1
    return index >= 0 and position < spans[index][1]


def _sections(body: str, item_id: str, findings: list[PmFinding]) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(body))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group("title").strip().lower()
        if title in result:
            findings.append(PmFinding("duplicate_section", f"{item_id} has duplicate {title} section", severity="blocking", item_id=item_id))
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[title] = body[match.end():end].strip()
    return result


def _section_bullets(source: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in source.splitlines():
        match = re.match(r"^\s*[-*+]\s+(?P<text>.+?)\s*$", line)
        if match:
            values.append(match.group("text"))
        elif values and (line.startswith(" ") or line.startswith("\t")) and line.strip():
            values[-1] = values[-1] + "\n" + line.strip()
    return tuple(values)


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _integer(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def render_task_block(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "")
    is_group = str(task.get("kind") or "").casefold() == "group"
    prefix = "G-" if is_group else "T-"
    if not _ID_RE.fullmatch(task_id) or not task_id.startswith(prefix):
        raise PmError("invalid_id", f"new {'group' if is_group else 'task'} requires a permanent {prefix} id")
    title = str(task.get("title") or "").strip()
    if not title:
        raise PmError("invalid_field", "new task title is required")
    if is_group:
        metadata = {
            "id": task_id,
            "parent_id": task.get("parent_id"),
            "order": _integer(task.get("order"), 0),
        }
        yaml_text = yaml.safe_dump(
            metadata, sort_keys=False, allow_unicode=True
        ).rstrip()
        return (
            f"<!-- symphony-group:{task_id} -->\n## {title}\n"
            f"```yaml symphony-group\n{yaml_text}\n```\n"
            "<!-- /symphony-group -->"
        )
    dependencies = task.get("dependencies") or []
    metadata = {
        "id": task_id,
        "parent_id": task.get("parent_id"),
        "order": _integer(task.get("order"), 0),
        "type": str(task.get("kind") or "Feature"),
        "priority": task.get("priority"),
        "human_owner": task.get("human_owner"),
        "agent_kind": task.get("agent_kind_override"),
        "depends_on": dependencies,
        "requirements": task.get("requirement_refs") or [],
        "storyboards": task.get("storyboard_refs") or [],
        "design_refs": task.get("design_refs") or [],
        "target_date": task.get("target_date"),
        "estimate": task.get("estimate"),
    }
    if task.get("verification_policy") is not None:
        metadata["verification_policy"] = {
            "version": 1,
            "procedures": [
                asdict(item) if isinstance(item, VerificationProcedureSelection) else dict(item)
                for item in task.get("verification_policy") or []
            ],
        }
    if task.get("cancelled") is True:
        metadata["cancelled"] = True
        metadata["cancellation_reason"] = task.get("cancellation_reason")
        metadata["successors"] = task.get("successor_refs") or task.get("successors") or []
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip()
    sections = [
        ("Scope", str(task.get("description") or "")),
        ("Acceptance criteria", _render_section_value("acceptance", task.get("acceptance") or [])),
        ("Verification", _render_section_value("verification", task.get("verification") or [])),
        ("Integration impact", str(task.get("integration_impact") or "")),
        ("Risk", str(task.get("risk") or "")),
        ("Notes", str(task.get("notes") or "")),
    ]
    detail = "\n\n".join(f"### {name}\n{value.strip()}" for name, value in sections if value.strip())
    task_line = f"- Cancelled: {title}" if task.get("cancelled") is True else f"- [ ] {title}"
    return (
        f"<!-- symphony-task:{task_id} -->\n{task_line}\n"
        f"```yaml symphony-task\n{yaml_text}\n```\n\n{detail}\n"
        "<!-- /symphony-task -->"
    )


def _metadata_findings(
    kind: str, item_id: str, metadata: dict[str, Any]
) -> list[PmFinding]:
    findings: list[PmFinding] = []
    expected_prefix = "G-" if kind == "group" else "T-"
    if not item_id.startswith(expected_prefix):
        findings.append(
            PmFinding(
                "identity_kind_mismatch",
                f"{kind} marker requires {expected_prefix} id",
                severity="blocking",
                item_id=item_id,
            )
        )
    parent = metadata.get("parent_id")
    if parent is not None and not isinstance(parent, str):
        findings.append(PmFinding("invalid_metadata_type", f"{item_id} parent_id must be text or null", severity="blocking", item_id=item_id, field="parent_id"))
    order = metadata.get("order")
    if isinstance(order, bool) or not isinstance(order, int):
        findings.append(PmFinding("invalid_metadata_type", f"{item_id} order must be an integer", severity="blocking", item_id=item_id, field="order"))
    for field in ("depends_on", "requirements", "storyboards", "design_refs"):
        value = metadata.get(field, [])
        if not isinstance(value, list):
            findings.append(PmFinding("invalid_metadata_type", f"{item_id} {field} must be a list", severity="blocking", item_id=item_id, field=field))
            continue
        if field != "depends_on" and not all(isinstance(entry, str) for entry in value):
            findings.append(PmFinding("invalid_metadata_type", f"{item_id} {field} entries must be text", severity="blocking", item_id=item_id, field=field))
        if field == "depends_on" and not all(
            isinstance(entry, str)
            or (
                isinstance(entry, dict)
                and isinstance(entry.get("task_id"), str)
                and entry.get("kind", "requires_code") in {"requires_code", "order_only"}
            )
            for entry in value
        ):
            findings.append(PmFinding("invalid_metadata_type", f"{item_id} dependency entries are invalid", severity="blocking", item_id=item_id, field=field))
    cancelled = metadata.get("cancelled", False)
    if not isinstance(cancelled, bool):
        findings.append(PmFinding("invalid_metadata_type", f"{item_id} cancelled must be boolean", severity="blocking", item_id=item_id, field="cancelled"))
    if cancelled and not isinstance(metadata.get("cancellation_reason"), str):
        findings.append(PmFinding("cancellation_reason_required", f"{item_id} cancellation requires a reason", severity="blocking", item_id=item_id, field="cancellation_reason"))
    successors = metadata.get("successors", [])
    if not isinstance(successors, list) or not all(isinstance(item, str) for item in successors):
        findings.append(PmFinding("invalid_metadata_type", f"{item_id} successors must be a text list", severity="blocking", item_id=item_id, field="successors"))
    if "verification_policy" in metadata:
        policy = metadata["verification_policy"]
        valid = (
            isinstance(policy, dict)
            and set(policy) == {"version", "procedures"}
            and type(policy.get("version")) is int
            and policy.get("version") == 1
            and isinstance(policy.get("procedures"), list)
        )
        procedures = policy.get("procedures", []) if isinstance(policy, dict) else []
        valid = valid and all(
            isinstance(entry, dict)
            and set(entry) == {"procedure_id", "kind"}
            and isinstance(entry.get("procedure_id"), str)
            and bool(entry["procedure_id"].strip())
            and entry.get("kind") in {"native", "manual"}
            for entry in procedures
        )
        ids = [entry.get("procedure_id") for entry in procedures if isinstance(entry, dict)]
        if not valid or len(ids) != len(set(ids)):
            findings.append(PmFinding(
                "invalid_verification_policy",
                f"{item_id} verification_policy must be version 1 with unique typed procedure ids",
                severity="blocking", item_id=item_id, field="verification_policy",
            ))
    return findings


def _verification_policy(
    metadata: dict[str, Any],
) -> tuple[VerificationProcedureSelection, ...] | None:
    if "verification_policy" not in metadata:
        return None
    raw = metadata["verification_policy"]
    if not isinstance(raw, dict):
        return ()
    procedures = raw.get("procedures", [])
    return tuple(
        VerificationProcedureSelection(str(item["procedure_id"]), item["kind"])
        for item in procedures
    )
