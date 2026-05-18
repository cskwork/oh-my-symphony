"""SPEC §5.1, §5.2 — WORKFLOW.md path resolution + front-matter parsing.

`WorkflowDefinition` is the raw byproduct of reading WORKFLOW.md: an
already-parsed YAML front-matter dict plus the trimmed prompt body. The
config builder lives in `builder.py` and turns this into the strongly
typed `ServiceConfig`.

We split parse-from-text and load-from-disk because tests can drive the
former with inline strings, while the orchestrator goes through the
disk-bound `load_workflow` path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..errors import (
    MissingWorkflowFile,
    WorkflowFrontMatterNotAMap,
    WorkflowParseError,
)


@dataclass(frozen=True)
class WorkflowDefinition:
    """§4.1.2 — parsed WORKFLOW.md payload."""

    config: dict[str, Any]
    prompt_template: str
    source_path: Path

    def base_dir(self) -> Path:
        return self.source_path.parent


def parse_workflow_text(text: str, source_path: Path) -> WorkflowDefinition:
    """§5.2 — front-matter delimited by `---` lines, trim body."""
    lines = text.splitlines()
    config: dict[str, Any] = {}
    body_lines = lines

    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration as exc:
            raise WorkflowParseError(
                "front matter not terminated", source=str(source_path)
            ) from exc
        front_text = "\n".join(lines[1:end])
        body_lines = lines[end + 1 :]
        if front_text.strip():
            try:
                parsed = yaml.safe_load(front_text)
            except yaml.YAMLError as exc:
                raise WorkflowParseError(
                    "invalid YAML front matter", source=str(source_path), error=str(exc)
                ) from exc
            if parsed is None:
                config = {}
            elif not isinstance(parsed, dict):
                raise WorkflowFrontMatterNotAMap(
                    "front matter must decode to a map", source=str(source_path)
                )
            else:
                config = parsed
    body = "\n".join(body_lines).strip()
    return WorkflowDefinition(config=config, prompt_template=body, source_path=source_path)


def load_workflow(path: str | Path) -> WorkflowDefinition:
    """§5.1 — read WORKFLOW.md from explicit path."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MissingWorkflowFile("workflow file not found", path=str(p)) from exc
    except OSError as exc:
        raise MissingWorkflowFile(
            "workflow file unreadable", path=str(p), error=str(exc)
        ) from exc
    return parse_workflow_text(text, p.resolve())


def resolve_workflow_path(explicit: str | Path | None) -> Path:
    """§5.1 — explicit path else `./WORKFLOW.md`."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path.cwd() / "WORKFLOW.md"
