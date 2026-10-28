"""Regenerate packaged copies: python -m symphony.templates.project._sync.

The four-lane prompts and the worktree setup script stay at the repository
root because the root WORKFLOW.md and the examples resolve them there; the
package carries copies for non-editable installs and a parity test prevents
drift. The operator skill and the project AGENTS.md live only in the package.
"""

from pathlib import Path
import shutil

import yaml


# Per-agent command blocks travel with the bundle so a `defaults: standard`
# board launches every agent exactly like the full example (for instance the
# Claude `--add-dir` grant that lets workers write the linked host board).
AGENT_BLOCKS = ("codex", "claude", "gemini", "agy", "kiro", "opencode", "pi", "prime_agent")


def source_files(root: Path) -> list[tuple[Path, str]]:
    pairs = [
        (
            root / "scripts/symphony-setup-worktree.sh",
            "scripts/symphony-setup-worktree.sh",
        ),
    ]
    for relative in ("docs/symphony-prompts/file",):
        for path in sorted((root / relative).rglob("*")):
            if (
                path.is_file()
                and path.name != ".DS_Store"
                and "__pycache__" not in path.parts
            ):
                pairs.append((path, str(path.relative_to(root))))
    return pairs


def main() -> None:
    root = Path(__file__).resolve().parents[4]
    destination = Path(__file__).parent
    for source, relative in source_files(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    text = (root / "examples/advanced/WORKFLOW.file.example.md").read_text(
        encoding="utf-8"
    )
    config = yaml.safe_load(text.split("---", 2)[1])
    (destination / "defaults.yaml").write_text(
        yaml.safe_dump(
            {key: config[key] for key in ("tracker", "prompts", "hooks", *AGENT_BLOCKS)},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
