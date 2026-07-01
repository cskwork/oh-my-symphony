"""Skill discovery + prompt injection for skill-attached issues.

A skill is a directory containing SKILL.md with optional YAML frontmatter
(`name`, `description`). Skills live under `<workflow_dir>/skills/`. Issues
reference them by name in a `skills:` frontmatter list; at dispatch the
orchestrator appends each attached skill's body to the first-turn prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# A single skill body larger than this is truncated on injection so one
# oversized SKILL.md cannot crowd the stage prompt out of the context window.
MAX_SKILL_CHARS = 20_000

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path

    def body(self) -> str:
        """Return the SKILL.md content without frontmatter, size-capped."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return ""
        _, body = _split_frontmatter(text)
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS] + "\n\n[skill truncated]"
        return body.strip()


def _split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                parsed = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return {}, text
            front = parsed if isinstance(parsed, dict) else {}
            return front, "\n".join(lines[i + 1 :])
    return {}, text


def skills_root(workflow_dir: Path) -> Path:
    return workflow_dir / "skills"


def list_skills(workflow_dir: Path) -> list[Skill]:
    """Scan `<workflow_dir>/skills/*/SKILL.md`, sorted by name."""
    root = skills_root(workflow_dir)
    if not root.is_dir():
        return []
    out: list[Skill] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        front, _ = _split_frontmatter(text)
        raw_name = front.get("name") or skill_md.parent.name
        name = str(raw_name).strip().lower()
        if not _NAME_RE.match(name):
            name = skill_md.parent.name.lower()
        out.append(
            Skill(
                name=name,
                description=str(front.get("description") or "").strip(),
                path=skill_md,
            )
        )
    return out


def normalize_skill_names(value: object) -> tuple[str, ...]:
    """Coerce a frontmatter `skills:` value to a validated name tuple."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name and _NAME_RE.match(name) and name not in out:
            out.append(name)
    return tuple(out)


def render_skill_block(workflow_dir: Path, skill_names: tuple[str, ...]) -> str:
    """Return the `## Attached skills` prompt section, or "" when none apply.

    Unknown names are listed as unavailable rather than silently dropped so
    the agent (and the operator reading logs) can see the mismatch.
    """
    if not skill_names:
        return ""
    available = {s.name: s for s in list_skills(workflow_dir)}
    parts: list[str] = ["## Attached skills", ""]
    parts.append(
        "The operator attached these skills to this ticket. Follow their "
        "instructions while working on it."
    )
    for name in skill_names:
        skill = available.get(name)
        if skill is None:
            parts.append(f"\n### {name}\n\n(skill not found under skills/)")
            continue
        body = skill.body() or "(empty skill)"
        parts.append(f"\n### {name}\n\n{body}")
    return "\n".join(parts)
