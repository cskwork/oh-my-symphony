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

from ..errors import ConfigValidationError, SymphonyError
from .builder import _validated_agent_profiles, validated_ci_modes
from .constants import DEFAULT_CI_MIN_INTERVAL_MS, selectable_agent_kinds
from .presets import LanePreset, get_lane_preset

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
    # ruamel.yaml's inferred attributes are typed as None, but these options
    # accept their documented runtime values. Keep the workaround local rather
    # than weakening type checking for the module.
    setattr(y, "preserve_quotes", True)
    # Long hook lines must not be re-wrapped — that corrupts shell blocks.
    setattr(y, "width", 100_000)
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
    `agent.max_total_tokens_by_state`, `agent.stall_timeout_ms_by_state`,
    `agent.stage_kinds`) consistent. New
    active columns get a
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
        _rename_state_keyed_map(agent, "stall_timeout_ms_by_state", renamed, removed)
        _rename_state_keyed_map(agent, "stage_kinds", renamed, removed)

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
# branch policy (ported from legacy board-viewer text surgery, now round-trip)
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


# ---------------------------------------------------------------------------
# continuous improvement heartbeat
# ---------------------------------------------------------------------------


def set_continuous_improvement_settings(
    workflow_path: Path,
    *,
    enabled: bool | None = None,
    interval_ms: int | None = None,
    max_turns: int | None = None,
    agent_kind: str | None = None,
    modes: list[str] | None = None,
) -> None:
    """Update the settable subset of `continuous_improvement:` in WORKFLOW.md.

    Only `enabled`, `interval_ms`, `max_turns`, `agent_kind`, and `modes` are
    writable here — the remaining fields (`ticket_prefix`,
    `max_tickets_per_run`, `mode_interval_hours`,
    `max_improvement_tickets_per_run`, `require_idle_board`) are parse-only
    and must be hand-edited. Omitted keyword arguments leave the existing
    value untouched; `agent_kind=""` explicitly clears back to inheriting the
    workflow's default agent, and `modes=[]` clears back to readiness-only.
    """
    if enabled is not None and not isinstance(enabled, bool):
        raise WorkflowMutationError("continuous_improvement.enabled must be a boolean")
    if interval_ms is not None:
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int):
            raise WorkflowMutationError(
                "continuous_improvement.interval_ms must be an integer"
            )
        if interval_ms < DEFAULT_CI_MIN_INTERVAL_MS:
            raise WorkflowMutationError(
                f"continuous_improvement.interval_ms must be >= {DEFAULT_CI_MIN_INTERVAL_MS}"
            )
    if max_turns is not None:
        if isinstance(max_turns, bool) or not isinstance(max_turns, int):
            raise WorkflowMutationError(
                "continuous_improvement.max_turns must be an integer"
            )
        if max_turns < 0:
            raise WorkflowMutationError(
                "continuous_improvement.max_turns must be >= 0"
            )
    normalized_agent_kind: str | None = None
    if agent_kind is not None:
        if not isinstance(agent_kind, str):
            raise WorkflowMutationError(
                "continuous_improvement.agent_kind must be a string"
            )
        normalized_agent_kind = agent_kind.strip().lower()

    normalized_modes: list[str] | None = None
    if modes is not None:
        try:
            normalized_modes = list(validated_ci_modes(modes))
        except ConfigValidationError as exc:
            raise WorkflowMutationError(exc.message) from exc

    data, body = _load_frontmatter(workflow_path)
    if normalized_agent_kind:
        agent = data.get("agent") or {}
        if not isinstance(agent, dict):
            raise WorkflowMutationError("agent must be a map")
        try:
            profiles = _validated_agent_profiles(agent.get("profiles"))
        except ConfigValidationError as exc:
            raise WorkflowMutationError(exc.message) from exc
        kinds = selectable_agent_kinds(profiles)
        if normalized_agent_kind not in kinds:
            raise WorkflowMutationError(
                f"unknown continuous_improvement.agent_kind {normalized_agent_kind!r}; "
                f"supported: {sorted(kinds)}"
            )
    ci = _ensure_map(data, "continuous_improvement")
    if enabled is not None:
        ci["enabled"] = enabled
    if interval_ms is not None:
        ci["interval_ms"] = interval_ms
    if max_turns is not None:
        ci["max_turns"] = max_turns
    if normalized_agent_kind is not None:
        ci["agent_kind"] = normalized_agent_kind
    if normalized_modes is not None:
        if normalized_modes:
            ci["modes"] = normalized_modes
        else:
            ci.pop("modes", None)
    _write_workflow_atomic(workflow_path, data, body)


# ---------------------------------------------------------------------------
# lane presets
# ---------------------------------------------------------------------------


def apply_lane_preset(workflow_path: Path, preset_name: str) -> StatesUpdatePlan:
    """Apply a shipped lane preset as the board's new starting point.

    Sets `tracker.active_states` to the preset lanes exactly, merges
    `tracker.terminal_states` (preset lanes first, user extras kept),
    rewrites `state_descriptions` (preset text for preset lanes, carried
    text for surviving user lanes), and points `prompts.base` +
    `prompts.stages` at the preset's prompt files. Everything else in the
    frontmatter — comments, agent policy, hooks, per-state maps for
    surviving lanes — is untouched, so a preset is a starting point and
    the board stays fully customizable afterwards.

    Old active lanes that are not part of the preset are removed (their
    per-state map entries drop, matching `apply_states_update`); the
    caller migrates their tickets to `plan.fallback_state`. Preset prompt
    files ship under `docs/symphony-prompts/file/`; when a mapped file is
    missing on this board a starter prompt is created so the workflow
    stays loadable.

    Returns the change plan; the caller migrates board tickets accordingly.
    """
    try:
        preset = get_lane_preset(preset_name)
    except ValueError as exc:
        raise WorkflowMutationError(str(exc)) from exc

    # Fail before touching WORKFLOW.md: a board without the preset's prompt
    # bodies would otherwise switch lanes and lose every gate (F-11).
    _require_preset_prompt_files(workflow_path, preset)

    data, body = _load_frontmatter(workflow_path)
    tracker = _ensure_map(data, "tracker")
    old_active = [str(s) for s in tracker.get("active_states") or []]
    old_terminal = [str(s) for s in tracker.get("terminal_states") or []]
    old_names = {n.lower(): n for n in old_active + old_terminal}

    preset_lanes_lower = {
        s.lower() for s in preset.active_states + preset.terminal_states
    }
    extra_terminal = [t for t in old_terminal if t.lower() not in preset_lanes_lower]
    new_active = list(preset.active_states)
    new_terminal = list(preset.terminal_states) + extra_terminal

    new_names_lower = {s.lower() for s in new_active + new_terminal}
    removed = [
        original
        for low, original in old_names.items()
        if low not in new_names_lower
    ]
    added = [
        name
        for name in new_active + new_terminal
        if name.lower() not in old_names
    ]

    tracker["active_states"] = _flow_seq(new_active)
    tracker["terminal_states"] = _flow_seq(new_terminal)

    old_descriptions = {
        str(k).lower(): str(v)
        for k, v in (tracker.get("state_descriptions") or {}).items()
    }
    descriptions = CommentedMap()
    for name in new_active + new_terminal:
        text = preset.state_descriptions.get(name) or old_descriptions.get(
            name.lower()
        )
        if text:
            descriptions[name] = text
    if descriptions or "state_descriptions" in tracker:
        tracker["state_descriptions"] = descriptions

    prompts = _ensure_map(data, "prompts")
    prompts["base"] = preset.base_prompt
    stages = _ensure_map(prompts, "stages")
    _apply_key_changes(stages, {}, removed)
    # Keep stage entries only for the preset's lanes and the user's own
    # extra lanes — a leftover entry from the previous preset (e.g. the
    # 4-lane Done report prompt on the deep board) would carry the wrong
    # stage semantics.
    allowed = {s.lower() for s in preset.stage_prompts} | {
        t.lower() for t in extra_terminal
    }
    for existing in list(stages.keys()):
        if str(existing).lower() not in allowed:
            stages.pop(existing)
    for state, rel in preset.stage_prompts.items():
        for existing in list(stages.keys()):
            if str(existing).lower() == state.lower() and str(existing) != state:
                stages.pop(existing)
        stages[state] = rel

    agent = data.get("agent")
    if isinstance(agent, dict):
        _rename_state_keyed_map(agent, "max_concurrent_agents_by_state", {}, removed)
        _rename_state_keyed_map(agent, "max_state_turns_by_state", {}, removed)
        _rename_state_keyed_map(agent, "max_total_tokens_by_state", {}, removed)
        _rename_state_keyed_map(agent, "stall_timeout_ms_by_state", {}, removed)
        _rename_state_keyed_map(agent, "stage_kinds", {}, removed)

    _write_workflow_atomic(workflow_path, data, body)
    return StatesUpdatePlan(
        renamed={},
        removed=removed,
        added=added,
        fallback_state=new_active[0],
    )


def _require_preset_prompt_files(workflow_path: Path, preset: LanePreset) -> None:
    """Refuse a preset apply when the board lacks the preset's prompt bodies.

    F-11: this used to write placeholder stubs ("Do the work this column
    stands for…") and report success. That is the normal state of any repo
    bootstrapped before a preset shipped, so the board silently degraded from
    a gated pipeline to "do something" — with the mutation already written.
    Missing bodies that the package bundles are copied into the board; only
    files the package does not ship are refused, naming them and the one
    command that fixes them.
    """
    workflow_dir = workflow_path.parent.resolve()

    def _resolved(rel: str) -> Path:
        path = (workflow_dir / rel).resolve()
        if not path.is_relative_to(workflow_dir):
            raise WorkflowMutationError(
                f"prompt path escapes the workflow directory: {rel}"
            )
        return path

    missing = [
        rel
        for rel in (preset.base_prompt, *preset.stage_prompts.values())
        if not _resolved(rel).is_file()
    ]
    if not missing:
        return
    # Projects created with `defaults: standard` carry no prompt files; the
    # package ships every preset's prompt bodies, so copy them in, but only
    # when all of them are available (all or nothing).
    from ..templates.project import resource_path

    bundled = {rel: resource_path(rel.removeprefix("./")) for rel in missing}
    if all(source.is_file() for source in bundled.values()):
        for rel, source in bundled.items():
            target = _resolved(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        return
    raise WorkflowMutationError(
        f"lane preset {preset.name!r} needs prompt files this board does not "
        f"have: {', '.join(sorted(missing))}. Copy them from the Symphony "
        "checkout first: `cp -R <symphony>/docs/symphony-prompts "
        f"{workflow_dir}/docs/`. Nothing was changed."
    )
