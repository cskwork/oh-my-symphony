"""Lane presets — switchable starting-point board layouts.

Two shipped presets (v2.1 owner decisions, docs/plans/minimal-symphony-plan.md):

- ``default`` — the succinct 4-lane board (Todo → In Progress → Verify →
  Document) whose stage contracts `orchestrator/contracts.py` enforces.
- ``deep`` — the optional 8-lane pipeline (Intake → Research → Plan →
  Review → Build → QA → Verify → Document) ported from the OneShot
  template, carrying its own lean bash gates per lane.

Deep preset merge contract
--------------------------

Every lane of the deep preset is a *separate ticket*, so each gets its own
worktree on its own ``symphony/<ID>`` branch. Downstream lanes (QA, Verify,
Document) can only see a Build slice's code if that slice has already landed
on the branch their worktree is cut from. The preset therefore requires:

* ``agent.auto_merge_on_done: true`` — the orchestrator merges each slice
  when its ticket reaches ``Done``; nothing else merges (the Verify lane
  proves, it does not merge — see the single-merge rule in
  ``docs/PIPELINE.md``).
* ``agent.feature_base_branch == agent.auto_merge_target_branch`` — new
  worktrees must start from the branch the merges land on. Both default to
  the host repo's current branch, which satisfies this; setting only one of
  them breaks it.
* Build merges are gated by the **Review** lane's ``verdict: PASS``, not by
  Verify: spawned Build tickets stay ``blocked_by`` the request ticket, and
  the request ticket only reaches ``Done`` when Review passes. A Verify
  ``verdict: RED`` reopens the offending Build tickets, so the *next* merge
  is blocked by the reopened slice, not by rewinding an existing one.

``symphony doctor`` reports this contract as ``board.deep_merge_contract``.

The deep preset intentionally ships **no ``Done`` stage prompt**: every lane
is its own ticket that ends at ``Done``, so a Done-lane report would duplicate
the lane's own gate. ``apply_lane_preset`` therefore drops the default
preset's ``Done`` entry when switching to deep, and restores it on the way
back.

A preset is a *starting point*, never a cage: `apply_lane_preset` in
`workflow.mutate` writes these values into WORKFLOW.md through the same
comment-preserving round-trip the lane CRUD uses, and every per-column
prompt edit or lane change afterwards works exactly as before.

Prompt paths are relative to the workflow file's directory and point at
the prompt sets shipped under ``docs/symphony-prompts/file/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping


@dataclass(frozen=True)
class LanePreset:
    """One switchable board layout: lanes, descriptions, prompt files."""

    name: str
    label: str
    active_states: tuple[str, ...]
    terminal_states: tuple[str, ...]
    state_descriptions: Mapping[str, str]
    base_prompt: str
    stage_prompts: Mapping[str, str]


_DEFAULT_STAGE_DIR = "./docs/symphony-prompts/file/stages"
_DEEP_STAGE_DIR = "./docs/symphony-prompts/file/deep"

DEFAULT_PRESET = LanePreset(
    name="default",
    label="4-lane default (Todo → In Progress → Verify → Document)",
    active_states=("Todo", "In Progress", "Verify", "Document"),
    terminal_states=("Human Review", "Done", "Blocked", "Archive"),
    state_descriptions=MappingProxyType(
        {
            "Todo": "Triage; route to In Progress",
            "In Progress": "Plan + TDD implementation + self-critique",
            "Verify": "Review + QA + Merge Gate",
            "Document": "Docs + wiki write-back; Done unless intervention",
            "Human Review": "Manual intervention or explicit review before Done",
            "Done": "Verified complete",
        }
    ),
    base_prompt="./docs/symphony-prompts/file/base.md",
    stage_prompts=MappingProxyType(
        {
            "Todo": f"{_DEFAULT_STAGE_DIR}/todo.md",
            "In Progress": f"{_DEFAULT_STAGE_DIR}/in-progress.md",
            "Verify": f"{_DEFAULT_STAGE_DIR}/verify.md",
            "Document": f"{_DEFAULT_STAGE_DIR}/document.md",
            "Done": f"{_DEFAULT_STAGE_DIR}/done.md",
        }
    ),
)

DEEP_PRESET = LanePreset(
    name="deep",
    label="8-lane deep pipeline (Intake → Research → Plan → Review → Build → QA → Verify → Document)",
    active_states=(
        "Intake",
        "Research",
        "Plan",
        "Review",
        "Build",
        "QA",
        "Verify",
        "Document",
    ),
    terminal_states=("Done", "Human Review", "Blocked", "Cancelled"),
    state_descriptions=MappingProxyType(
        {
            "Intake": "Brief + work-type routing",
            "Research": "Evidence: stack, prior art, data shapes, repro",
            "Plan": "Decompose into a Build/QA/Verify/Document DAG",
            "Review": "Adversarial plan red-team; PASS releases builds",
            "Build": "TDD one slice; append claims.md",
            "QA": "Black-box behavioral/browser proof",
            "Verify": "Re-prove every claim; verdict GREEN gate",
            "Document": "Docs/CHANGELOG + wiki write-back + delivery.md",
            "Human Review": "Manual intervention or explicit review before Done",
            "Done": "Lane gate passed",
        }
    ),
    base_prompt=f"{_DEEP_STAGE_DIR}/base.md",
    stage_prompts=MappingProxyType(
        {
            "Intake": f"{_DEEP_STAGE_DIR}/intake.md",
            "Research": f"{_DEEP_STAGE_DIR}/research.md",
            "Plan": f"{_DEEP_STAGE_DIR}/plan.md",
            "Review": f"{_DEEP_STAGE_DIR}/review.md",
            "Build": f"{_DEEP_STAGE_DIR}/build.md",
            "QA": f"{_DEEP_STAGE_DIR}/qa.md",
            "Verify": f"{_DEEP_STAGE_DIR}/verify.md",
            "Document": f"{_DEEP_STAGE_DIR}/document.md",
        }
    ),
)

LANE_PRESETS: Mapping[str, LanePreset] = MappingProxyType(
    {preset.name: preset for preset in (DEFAULT_PRESET, DEEP_PRESET)}
)


def preset_names() -> tuple[str, ...]:
    return tuple(LANE_PRESETS)


def get_lane_preset(name: str) -> LanePreset:
    """Look up a preset by name; raises ValueError with the known names."""
    key = (name or "").strip().lower()
    preset = LANE_PRESETS.get(key)
    if preset is None:
        raise ValueError(
            f"unknown lane preset {name!r}; available: {', '.join(LANE_PRESETS)}"
        )
    return preset


def guess_lane_preset(active_states: Iterable[str]) -> str | None:
    """Best-effort match of a board's active lanes to a shipped preset.

    Case-insensitive exact sequence match — a customized board (extra,
    renamed, or reordered lanes) intentionally matches nothing.
    """
    current = tuple(str(s).strip().lower() for s in active_states)
    for preset in LANE_PRESETS.values():
        if current == tuple(s.lower() for s in preset.active_states):
            return preset.name
    return None


# Lanes the shipped stage-contract validator (orchestrator/contracts.py)
# enforces: the DEFAULT preset's active lanes plus the legacy `Learn`
# name — the pre-rename spelling of Document that existing boards keep.
# Boards with any other active lane (deep preset's Intake/Research/Plan/
# Review/Build/QA, or user-defined lanes) carry their own prompt-encoded
# gates, so enforcing the default section lists against them would rewind
# tickets for sections their prompts never asked for.
_CONTRACT_LANES = frozenset(
    {state.lower() for state in DEFAULT_PRESET.active_states} | {"learn"}
)


def board_uses_default_contracts(
    active_states: "tuple[str, ...] | list[str]",
) -> bool:
    """True when every active lane belongs to the default-preset lane set.

    Workflow-domain concern (it validates lane names against the default
    preset), so it lives here rather than in the orchestrator layer that
    consumes it; `orchestrator/contracts.py` re-exports it for backwards
    compatibility.
    """
    return all(
        (state or "").strip().lower() in _CONTRACT_LANES for state in active_states
    )
