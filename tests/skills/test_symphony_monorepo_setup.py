"""Contract: the monorepo subfolder ships a parseable WORKFLOW.

`setup-monorepo.sh` deliberately does NOT emit a per-service WORKFLOW (that is
workspace-specific); it copies prompts, wires permissions, and points the user
at `references/workflow-template.md` for the WORKFLOW to author. The script has
no `--dry-run`/`--check` flag, and the spec says not to add one — so the
statically checkable promise is: the template the script directs you to is a
WORKFLOW symphony's own parser accepts, and it carries the worktree-based
`after_create` hook the skill advertises (S4 in
docs/improvements/supergoal-learnings-2026-06.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MONOREPO = REPO_ROOT / "skills" / "symphony-skill" / "monorepo"
SETUP = MONOREPO / "scripts" / "setup-monorepo.sh"
TEMPLATE = MONOREPO / "references" / "workflow-template.md"


def _fenced_yaml_workflow(md_text: str) -> str:
    """Extract the first ```yaml fenced block (the full WORKFLOW.md body)."""
    match = re.search(r"```yaml\n(.*?)\n```", md_text, re.DOTALL)
    assert match, "no ```yaml fenced WORKFLOW block found in workflow-template.md"
    return match.group(1)


def _parse_workflow_config(block: str) -> dict:
    """Parse the WORKFLOW front-matter. Prefer symphony's real parser so the
    test fails if the template drifts out of what the orchestrator accepts;
    fall back to direct YAML if symphony cannot be imported in this env.
    """
    try:
        from symphony.workflow.parser import parse_workflow_text
    except Exception:  # pragma: no cover - import availability varies
        front = block
        if block.lstrip().startswith("---"):
            parts = block.split("---")
            # parts[0] is '' (before first ---), parts[1] is the front-matter
            front = parts[1] if len(parts) >= 3 else block
        config = yaml.safe_load(front)
        assert isinstance(config, dict), "WORKFLOW front-matter is not a mapping"
        return config

    wf = parse_workflow_text(block, Path("WORKFLOW.template.md"))
    assert isinstance(wf.config, dict), "parsed WORKFLOW config is not a mapping"
    return wf.config


def test_setup_script_exists() -> None:
    assert SETUP.is_file(), f"missing setup script: {SETUP}"


def test_setup_points_at_workflow_template() -> None:
    # The script's value is that it directs the user to the authorable template.
    body = SETUP.read_text(encoding="utf-8")
    assert "workflow-template.md" in body, (
        "setup-monorepo.sh should reference references/workflow-template.md"
    )
    assert TEMPLATE.is_file(), f"referenced template missing: {TEMPLATE}"


def test_workflow_template_is_parseable_with_worktree_hook() -> None:
    block = _fenced_yaml_workflow(TEMPLATE.read_text(encoding="utf-8"))
    config = _parse_workflow_config(block)

    hooks = config.get("hooks")
    assert isinstance(hooks, dict), "WORKFLOW template has no hooks mapping"

    after_create = hooks.get("after_create")
    assert isinstance(after_create, str) and after_create.strip(), (
        "WORKFLOW template hooks.after_create is missing or empty"
    )

    # The advertised hook attaches the per-ticket workspace as a git worktree.
    assert "git" in after_create and "worktree add" in after_create, (
        "after_create hook must create a git worktree (found:\n"
        f"{after_create}\n)"
    )
