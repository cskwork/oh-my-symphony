"""User-driven WORKFLOW.md mutations (board columns, prompts, branch policy).

The web UI and TUI edit the workflow through this module only. Edits
round-trip the YAML frontmatter with ruamel.yaml so the file's comments and
key order — which operators hand-edit — survive every UI change. The body
below the frontmatter is never touched.

All writes are atomic (temp file + rename). Validation errors raise
`WorkflowMutationError` with a human-readable message the API returns as-is.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from ..errors import SymphonyError

_STATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _/-]{0,39}$")
_MAX_COLUMNS = 100

DEFAULT_STAGE_PROMPT = """You are working on {{ issue.identifier }}: {{ issue.title }}.
Current state: **{{ state_name }}**.

Do the work this column stands for, record progress in the ticket file, and
move the ticket to the next state when done.
"""


class WorkflowMutationError(SymphonyError):
    """Invalid user edit — message is safe to show verbatim in the UI."""

    code = "workflow_mutation_error"


@dataclass(frozen=True)
class StateSpec:
    """One kanban column as submitted by the UI.

    `description=None` means "not provided — keep whatever WORKFLOW.md
    already has"; an empty string explicitly clears the description.
    """

    name: str
    description: str | None = None
    terminal: bool = False
    # Set when the user renamed a column; tickets migrate from this name.
    previous_name: str | None = None


@dataclass(frozen=True)
class StatesUpdatePlan:
    """What `apply_states_update` changed — the caller migrates tickets."""

    renamed: dict[str, str] = field(default_factory=dict)  # old -> new
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    fallback_state: str = ""


# ---------------------------------------------------------------------------
# frontmatter round-trip plumbing
# ---------------------------------------------------------------------------


def _yaml_rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    # Long hook lines must not be re-wrapped — that corrupts shell blocks.
    y.width = 100_000
    return y


def _split_workflow(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, rest_after_closing_delimiter)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise WorkflowMutationError(
            "WORKFLOW.md has no YAML frontmatter; add a `---` block first"
        )
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    raise WorkflowMutationError("WORKFLOW.md frontmatter is not terminated")


def _load_frontmatter(path: Path) -> tuple[CommentedMap, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowMutationError(f"cannot read workflow file: {exc}") from exc
    front_text, body = _split_workflow(text)
    try:
        data = _yaml_rt().load(front_text)
    except YAMLError as exc:
        # A hand-edit typo must surface as a 400 with the parse error, not
        # as an unlogged 500 from the API layer.
        raise WorkflowMutationError(
            f"WORKFLOW.md frontmatter is not valid YAML: {exc}"
        ) from exc
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise WorkflowMutationError("workflow frontmatter must be a YAML map")
    return data, body


def _write_workflow_atomic(path: Path, data: CommentedMap, body: str) -> None:
    buf = io.StringIO()
    _yaml_rt().dump(data, buf)
    text = "---\n" + buf.getvalue().rstrip("\n") + "\n---\n" + body
    fd, tmp = tempfile.mkstemp(prefix=".tmp-workflow-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_map(parent: CommentedMap, key: str) -> CommentedMap:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = CommentedMap()
        parent[key] = value
    return value


def _flow_seq(items: list[str]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


# ---------------------------------------------------------------------------
# states (kanban columns)
# ---------------------------------------------------------------------------


def validate_states(specs: list[StateSpec]) -> None:
    if not specs:
        raise WorkflowMutationError("at least one column is required")
    if len(specs) > _MAX_COLUMNS:
        raise WorkflowMutationError(f"too many columns (max {_MAX_COLUMNS})")
    seen: set[str] = set()
    for spec in specs:
        name = spec.name.strip()
        if not _STATE_NAME_RE.match(name):
            raise WorkflowMutationError(
                f"invalid column name {name!r}: use letters, digits, spaces, "
                "-, _, / (max 40 chars)"
            )
        low = name.lower()
        if low in seen:
            raise WorkflowMutationError(f"duplicate column name {name!r}")
        seen.add(low)
    if not any(not s.terminal for s in specs):
        raise WorkflowMutationError("at least one active (non-terminal) column is required")
    if not any(s.terminal for s in specs):
        raise WorkflowMutationError("at least one terminal column is required")


def apply_states_update(workflow_path: Path, specs: list[StateSpec]) -> StatesUpdatePlan:
    """Persist a full ordered column list into WORKFLOW.md frontmatter.

    Handles add / remove / rename / reorder / description edits and keeps
    the per-state maps (`prompts.stages`, `agent.max_concurrent_agents_by_state`,
    `agent.max_total_tokens_by_state`) consistent. New active columns get a
    starter prompt file next to the existing stage prompts.

    Returns the change plan; the caller migrates board tickets accordingly.
    """
    specs = [
        StateSpec(
            name=s.name.strip(),
            description=s.description.strip() if s.description is not None else None,
            terminal=s.terminal,
            previous_name=(s.previous_name or "").strip() or None,
        )
        for s in specs
    ]
    validate_states(specs)
    data, body = _load_frontmatter(workflow_path)

    tracker = _ensure_map(data, "tracker")
    old_active = [str(s) for s in tracker.get("active_states") or []]
    old_terminal = [str(s) for s in tracker.get("terminal_states") or []]
    old_names = {n.lower(): n for n in old_active + old_terminal}

    renamed: dict[str, str] = {}
    added: list[str] = []
    for spec in specs:
        prev = spec.previous_name
        if prev and prev.lower() in old_names and prev.lower() != spec.name.lower():
            renamed[old_names[prev.lower()]] = spec.name
        elif spec.name.lower() not in old_names:
            added.append(spec.name)
    new_names_lower = {s.name.lower() for s in specs}
    reverse_renamed_lower = {old.lower() for old in renamed}
    removed = [
        original
        for low, original in old_names.items()
        if low not in new_names_lower and low not in reverse_renamed_lower
    ]

    tracker["active_states"] = _flow_seq([s.name for s in specs if not s.terminal])
    tracker["terminal_states"] = _flow_seq([s.name for s in specs if s.terminal])

    old_descriptions = {
        str(k).lower(): str(v)
        for k, v in (tracker.get("state_descriptions") or {}).items()
    }
    descriptions = CommentedMap()
    for spec in specs:
        if spec.description is None:
            # Not provided — keep the existing description (rename-aware).
            carried = old_descriptions.get((spec.previous_name or spec.name).lower())
            if carried:
                descriptions[spec.name] = carried
        elif spec.description:
            descriptions[spec.name] = spec.description
        # Empty string = explicit clear: no entry.
    if descriptions or "state_descriptions" in tracker:
        tracker["state_descriptions"] = descriptions

    _rename_map_keys(data, "prompts", "stages", renamed, removed)
    agent = data.get("agent")
    if isinstance(agent, dict):
        _rename_state_keyed_map(agent, "max_concurrent_agents_by_state", renamed, removed)
        _rename_state_keyed_map(agent, "max_state_turns_by_state", renamed, removed)
        _rename_state_keyed_map(agent, "max_total_tokens_by_state", renamed, removed)

    _add_stage_prompts(data, workflow_path, [s for s in specs if not s.terminal and s.name in added])

    _write_workflow_atomic(workflow_path, data, body)
    active_names = [s.name for s in specs if not s.terminal]
    return StatesUpdatePlan(
        renamed=renamed,
        removed=removed,
        added=added,
        fallback_state=active_names[0],
    )


def _rename_map_keys(
    data: CommentedMap,
    section: str,
    key: str,
    renamed: dict[str, str],
    removed: list[str],
) -> None:
    parent = data.get(section)
    if not isinstance(parent, dict):
        return
    mapping = parent.get(key)
    if not isinstance(mapping, dict):
        return
    _apply_key_changes(mapping, renamed, removed)


def _rename_state_keyed_map(
    parent: dict, key: str, renamed: dict[str, str], removed: list[str]
) -> None:
    mapping = parent.get(key)
    if not isinstance(mapping, dict):
        return
    _apply_key_changes(mapping, renamed, removed)


def _apply_key_changes(mapping: dict, renamed: dict[str, str], removed: list[str]) -> None:
    renamed_lower = {old.lower(): new for old, new in renamed.items()}
    removed_lower = {r.lower() for r in removed}
    for existing in list(mapping.keys()):
        low = str(existing).lower()
        if low in renamed_lower:
            mapping[renamed_lower[low]] = mapping.pop(existing)
        elif low in removed_lower:
            mapping.pop(existing)


def _add_stage_prompts(
    data: CommentedMap, workflow_path: Path, new_active: list[StateSpec]
) -> None:
    """Give each new active column a starter prompt file + stages entry.

    Only applies when the workflow already uses per-stage prompt files —
    workflows driven by the single body template are left alone.
    """
    prompts = data.get("prompts")
    if not isinstance(prompts, dict) or not new_active:
        return
    stages = prompts.get("stages")
    if not isinstance(stages, dict) or not stages:
        return
    sample = next((str(v) for v in stages.values() if isinstance(v, str) and v.strip()), None)
    if sample is None:
        return
    stage_dir_rel = Path(sample).parent
    workflow_dir = workflow_path.parent.resolve()
    for spec in new_active:
        slug = re.sub(r"[^a-z0-9]+", "-", spec.name.lower()).strip("-") or "stage"
        rel = stage_dir_rel / f"{slug}.md"
        abs_path = (workflow_dir / rel).resolve()
        if not abs_path.is_relative_to(workflow_dir):
            raise WorkflowMutationError(f"prompt path escapes the workflow directory: {rel}")
        if not abs_path.exists():
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            content = DEFAULT_STAGE_PROMPT.replace("{{ state_name }}", spec.name)
            abs_path.write_text(content, encoding="utf-8")
        stages[spec.name] = rel.as_posix()


# ---------------------------------------------------------------------------
# prompt files
# ---------------------------------------------------------------------------


def resolve_prompt_path(workflow_path: Path, state: str) -> Path | None:
    """Return the configured prompt file for `state`, guarded against escapes."""
    data, _ = _load_frontmatter(workflow_path)
    prompts = data.get("prompts")
    if not isinstance(prompts, dict):
        return None
    stages = prompts.get("stages")
    if not isinstance(stages, dict):
        return None
    for key, value in stages.items():
        if str(key).strip().lower() == state.strip().lower() and isinstance(value, str):
            workflow_dir = workflow_path.parent.resolve()
            path = (workflow_dir / value.strip()).resolve()
            if not path.is_relative_to(workflow_dir):
                raise WorkflowMutationError(
                    f"prompt path for {state!r} escapes the workflow directory"
                )
            return path
    return None


def read_prompt(workflow_path: Path, state: str) -> dict[str, object] | None:
    path = resolve_prompt_path(workflow_path, state)
    if path is None:
        return None
    workflow_dir = workflow_path.parent.resolve()
    try:
        content = path.read_text(encoding="utf-8")
        exists = True
    except OSError:
        content = ""
        exists = False
    return {
        "state": state,
        "path": path.relative_to(workflow_dir).as_posix(),
        "content": content,
        "exists": exists,
    }


def write_prompt(workflow_path: Path, state: str, content: str) -> Path:
    if len(content) > 512_000:
        raise WorkflowMutationError("prompt too large (max 512 KB)")
    path = resolve_prompt_path(workflow_path, state)
    if path is None:
        raise WorkflowMutationError(
            f"no prompt file is configured for column {state!r} "
            "(prompts.stages in WORKFLOW.md)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-prompt-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# branch policy (ported from tools/board-viewer text surgery, now round-trip)
# ---------------------------------------------------------------------------


def set_branch_policy(
    workflow_path: Path,
    *,
    feature_base_branch: str | None = None,
    auto_merge_target_branch: str | None = None,
) -> None:
    data, body = _load_frontmatter(workflow_path)
    agent = _ensure_map(data, "agent")
    if feature_base_branch is not None:
        agent["feature_base_branch"] = feature_base_branch.strip()
    if auto_merge_target_branch is not None:
        agent["auto_merge_target_branch"] = auto_merge_target_branch.strip()
    _write_workflow_atomic(workflow_path, data, body)
