"""Strict parser for Supergoal Wayfinder ticket files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..skills import normalize_skill_names

_ROUTES = {"GREENFIELD", "DEBUG", "LEGACY"}
_OVERLAYS = {"superdesign", "superpm", "superqa"}
_KIND_OVERLAYS = {
    "customer-research": "superpm",
    "research": "superpm",
    "design": ("superdesign", "superqa"),
    "product-spec": "superpm",
    "qa": "superqa",
    "ui": ("superdesign", "superqa"),
}
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class WayfinderTicket:
    key: str
    title: str
    route: str
    blocked_by: tuple[str, ...]
    skills: tuple[str, ...]
    description: str
    source_path: Path


def parse_wayfinder_ticket(path: Path) -> WayfinderTicket:
    text = path.read_text(encoding="utf-8")
    front, body = _split_frontmatter(text, path)
    key = _required_string(front, "id", path)
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            f"{path}: id must use only letters, numbers, dots, underscores, or hyphens"
        )
    title = _required_string(front, "title", path)
    route = _required_string(front, "route", path).upper()
    if route not in _ROUTES:
        raise ValueError(f"{path}: unsupported Route {route!r}")
    required_sections = (
        (("acceptance criteria",), "## Acceptance criteria"),
        (("proof commands", "proof"), "## Proof commands or ## Proof"),
        (("non-goals",), "## Non-goals"),
    )
    headings = {
        match.group(1).strip().lower()
        for match in re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE)
    }
    for alternatives, label in required_sections:
        if not any(name in headings for name in alternatives):
            raise ValueError(f"{path}: missing {label}")
    blockers = _string_list(front.get("blocked_by"), "blocked_by", path)
    invalid_blockers = [item for item in blockers if not _KEY_RE.fullmatch(item)]
    if invalid_blockers:
        raise ValueError(f"{path}: blocked_by contains invalid ids: {', '.join(invalid_blockers)}")
    explicit = _string_list(front.get("skills"), "skills", path)
    unsupported = sorted(set(explicit) - _OVERLAYS)
    if unsupported:
        raise ValueError(f"{path}: unsupported skill overlays: {', '.join(unsupported)}")
    inferred = _inferred_overlays(front, path)
    skills = normalize_skill_names(["supergoal", *inferred, *explicit])
    return WayfinderTicket(key, title, route, blockers, skills, body.strip(), path)


def _inferred_overlays(front: dict[str, object], path: Path) -> tuple[str, ...]:
    overlays: list[str] = []
    kind = front.get("kind")
    if kind is not None:
        if not isinstance(kind, str) or kind.strip().lower() not in _KIND_OVERLAYS:
            allowed = ", ".join(sorted(_KIND_OVERLAYS))
            raise ValueError(f"{path}: kind must be one of: {allowed}")
        inferred = _KIND_OVERLAYS[kind.strip().lower()]
        overlays.extend(inferred if isinstance(inferred, tuple) else (inferred,))
    browser = front.get("browser")
    if browser is not None and not isinstance(browser, bool):
        raise ValueError(f"{path}: browser must be a boolean")
    if browser:
        overlays.append("superqa")
    return tuple(overlays)


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: Wayfinder ticket must start with YAML frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"{path}: unterminated YAML frontmatter") from exc
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: YAML frontmatter must be a map")
    return parsed, "\n".join(lines[end + 1 :])


def _required_string(front: dict[str, object], key: str, path: Path) -> str:
    value = front.get(key)
    if not isinstance(value, str) or not value.strip():
        label = "Route" if key == "route" else key
        raise ValueError(f"{path}: missing {label}")
    return value.strip()


def _string_list(value: object, key: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{path}: {key} must be a YAML string list")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
