"""Markdown helpers for ticket bodies."""

from __future__ import annotations

import re


_DEPENDENCY_HEADING_RE = re.compile(
    r"^(?P<marks>#{2,6})\s+Dependencies\s*$", re.IGNORECASE
)
_FENCE_RE = re.compile(r"^\s{0,3}(?P<marker>`{3,}|~{3,})")
_MARKDOWN_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+\S")
_TICKET_ID_RE = re.compile(r"\b[A-Z][A-Z0-9_-]*-\d+\b")


def parse_body_dependency_ids(body: str | None) -> list[str]:
    if not body:
        return []
    dependency_ids: list[str] = []
    dependency_level: int | None = None
    fence_marker: str | None = None
    for raw_line in body.splitlines():
        marker = _fence_marker(raw_line)
        if fence_marker is not None:
            if (
                marker is not None
                and marker[0] == fence_marker[0]
                and len(marker) >= len(fence_marker)
            ):
                fence_marker = None
            continue
        if marker is not None:
            fence_marker = marker
            continue

        line = raw_line.strip()
        heading = _MARKDOWN_HEADING_RE.match(line)
        if heading is not None:
            level = len(heading.group("marks"))
            if dependency_level is not None and level <= dependency_level:
                break
            dependency = _DEPENDENCY_HEADING_RE.match(line)
            if dependency is not None:
                dependency_level = len(dependency.group("marks"))
                continue
        if dependency_level is not None:
            dependency_ids.extend(_TICKET_ID_RE.findall(line))
    return _dedupe_preserving_order(dependency_ids)


def _fence_marker(line: str) -> str | None:
    match = _FENCE_RE.match(line)
    if match is None:
        return None
    return match.group("marker")


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered
