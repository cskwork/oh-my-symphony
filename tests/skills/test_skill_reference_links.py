"""Contract: intra-bundle references in each SKILL.md resolve to real files.

The skills cite their own bundled files both as markdown links
(`[text](reference/foo.md)`) and as inline code spans (`` `reference/foo.md` ``).
A relative link here means a path whose first segment is one of the skill
bundle's own subdirectories (reference / references / templates / scripts);
that scoping deliberately excludes runtime/vault artifacts the prompts mention
but do not ship (e.g. `WORKFLOW.md`, `claims.md`, `.claude/settings.local.json`).

Catches the most common rot: a References section pointing at a file that was
renamed or never written (S4 in docs/improvements/supergoal-learnings-2026-06.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

SKILL_MD_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))

# Subdirectories a skill bundle uses to hold referenced files.
BUNDLE_SUBDIRS = {
    "assets",
    "monorepo",
    "oneshot",
    "reference",
    "references",
    "scripts",
    "templates",
}

# Markdown link target: [text](target)
_MD_LINK = re.compile(r"\]\(([^)]+)\)")
# Inline code span that looks like a path with an extension: `dir/file.ext`
_CODE_REF = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)`")


def _strip_anchor(target: str) -> str:
    # Drop URL fragments/queries so `reference/foo.md#section` resolves to the file.
    return target.split("#", 1)[0].split("?", 1)[0].strip()


def _is_relative_link(target: str) -> bool:
    if not target or target.startswith(("http://", "https://", "mailto:", "/")):
        return False
    if "/" not in target:
        return False  # bare filename — treated as a runtime artifact, not a bundle link
    return target.split("/", 1)[0] in BUNDLE_SUBDIRS


def _bundle_links(text: str) -> set[str]:
    targets: set[str] = set()
    for raw in _MD_LINK.findall(text):
        target = _strip_anchor(raw)
        if _is_relative_link(target):
            targets.add(target)
    for raw in _CODE_REF.findall(text):
        target = _strip_anchor(raw)
        if _is_relative_link(target):
            targets.add(target)
    return targets


def test_skills_dir_has_skill_files() -> None:
    assert SKILL_MD_FILES, f"no SKILL.md files found under {SKILLS_DIR}"


@pytest.mark.parametrize("skill_md", SKILL_MD_FILES, ids=lambda p: p.parent.name)
def test_relative_links_resolve(skill_md: Path) -> None:
    bundle = skill_md.parent
    text = skill_md.read_text(encoding="utf-8")
    missing = sorted(
        link for link in _bundle_links(text) if not (bundle / link).exists()
    )
    assert not missing, (
        f"{skill_md} references files that do not exist in the bundle: {missing}"
    )
