"""Contract: every bundled skill's SKILL.md carries valid frontmatter.

Ports the assertions from supergoal's `skill-frontmatter-gate.mjs`:
a SKILL.md must open with a `---` YAML front-matter block that parses to a
mapping with a non-empty `name` and `description`, and `name` must equal the
skill's directory name. A malformed or drifted frontmatter ships undetected
otherwise (S4 in docs/improvements/supergoal-learnings-2026-06.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

SKILL_MD_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _frontmatter(text: str) -> str:
    """Return the raw YAML between the leading `---` fences, or '' if absent."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx])
    return ""  # unterminated front-matter block


def test_skills_dir_has_skill_files() -> None:
    # Guard against the glob silently matching nothing (e.g. a moved tree),
    # which would make every parametrized test below vacuously pass.
    assert SKILL_MD_FILES, f"no SKILL.md files found under {SKILLS_DIR}"


def test_using_symphony_is_the_single_operator_router() -> None:
    assert [path.parent.name for path in SKILL_MD_FILES] == ["using-symphony"]


@pytest.mark.parametrize("skill_md", SKILL_MD_FILES, ids=lambda p: p.parent.name)
def test_skill_frontmatter_is_valid(skill_md: Path) -> None:
    raw = _frontmatter(skill_md.read_text(encoding="utf-8"))
    assert raw, f"{skill_md} is missing a leading `---` YAML front-matter block"

    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # pragma: no cover - failure path
        pytest.fail(f"{skill_md} front-matter is not valid YAML: {exc}")

    assert isinstance(meta, dict), f"{skill_md} front-matter must be a YAML mapping"

    name = meta.get("name")
    description = meta.get("description")

    assert isinstance(name, str) and name.strip(), (
        f"{skill_md} front-matter `name` must be a non-empty string"
    )
    assert isinstance(description, str) and description.strip(), (
        f"{skill_md} front-matter `description` must be a non-empty string"
    )
    assert name == skill_md.parent.name, (
        f"{skill_md} front-matter name {name!r} must match its directory "
        f"{skill_md.parent.name!r}"
    )
