"""workflow.presets — shipped lane preset definitions."""

from __future__ import annotations

import pytest

from symphony.workflow.presets import (
    DEEP_PRESET,
    DEFAULT_PRESET,
    LANE_PRESETS,
    get_lane_preset,
    guess_lane_preset,
    preset_names,
)


def test_two_presets_ship() -> None:
    assert preset_names() == ("default", "deep")
    assert LANE_PRESETS["default"] is DEFAULT_PRESET
    assert LANE_PRESETS["deep"] is DEEP_PRESET


def test_default_preset_matches_shipped_four_lane_board() -> None:
    assert DEFAULT_PRESET.active_states == ("Todo", "In Progress", "Verify", "Document")
    assert DEFAULT_PRESET.terminal_states == (
        "Human Review",
        "Done",
        "Blocked",
        "Archive",
    )
    assert DEFAULT_PRESET.base_prompt == "./docs/symphony-prompts/file/base.md"
    assert set(DEFAULT_PRESET.stage_prompts) == {
        "Todo",
        "In Progress",
        "Verify",
        "Document",
        "Done",
    }


def test_deep_preset_declares_eight_lane_pipeline() -> None:
    assert DEEP_PRESET.active_states == (
        "Intake",
        "Research",
        "Plan",
        "Review",
        "Build",
        "QA",
        "Verify",
        "Document",
    )
    assert "Cancelled" in DEEP_PRESET.terminal_states
    # Every active lane carries its own prompt file under deep/.
    for state in DEEP_PRESET.active_states:
        assert DEEP_PRESET.stage_prompts[state].startswith(
            "./docs/symphony-prompts/file/deep/"
        )


def test_preset_prompt_files_exist_in_repo() -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    for preset in LANE_PRESETS.values():
        assert (repo_root / preset.base_prompt).is_file(), preset.base_prompt
        for rel in preset.stage_prompts.values():
            assert (repo_root / rel).is_file(), rel


def test_get_lane_preset_is_case_insensitive_and_raises_on_unknown() -> None:
    assert get_lane_preset("Deep") is DEEP_PRESET
    assert get_lane_preset(" default ") is DEFAULT_PRESET
    with pytest.raises(ValueError, match="unknown lane preset"):
        get_lane_preset("mystery")


def test_guess_lane_preset_matches_exact_sequences_only() -> None:
    assert guess_lane_preset(["Todo", "in progress", "Verify", "Document"]) == "default"
    assert guess_lane_preset(DEEP_PRESET.active_states) == "deep"
    # Customized boards (extra / reordered lanes) match nothing.
    assert guess_lane_preset(["Todo", "Verify", "In Progress", "Document"]) is None
    assert guess_lane_preset(["Todo"]) is None


def test_deep_prompts_are_succinct_and_carry_the_gates() -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    deep_dir = repo_root / "src/symphony/templates/project" / "docs" / "symphony-prompts" / "file" / "deep"
    for rel in DEEP_PRESET.stage_prompts.values():
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert text.count("\n") <= 45, f"{rel} is not succinct"
    plan = (deep_dir / "plan.md").read_text(encoding="utf-8")
    # F-19: the CLI may live in a venv the worker's PATH does not carry.
    assert "${SYMPHONY_CLI:-symphony} board new" in plan
    assert "--blocked-by" in plan
    assert "--request" in plan
    assert "release-contract.yaml" in plan
    assert "app-release-finalizer" in plan
    review = (deep_dir / "review.md").read_text(encoding="utf-8")
    assert "verdict: PASS" in review
    assert "Max 2 objection rounds" in review
    assert "Human Review" in review
    verify = (deep_dir / "verify.md").read_text(encoding="utf-8")
    assert "grep -q '^verdict: GREEN'" in verify
    assert "symphony release check" in verify
    assert "historical" in verify.lower()
    assert '"$SYMPHONY_WORKFLOW_DIR/WORKFLOW.md"' in verify
    assert "structurally valid repairable RED" in verify
    assert "forward historical transition" in verify
    assert "Evidence, schema, or environment errors stay in `Verify`" in verify
    assert "rebase this evidence-only verifier branch" in verify
    assert "never merges it into the target" in verify
    assert "$SYMPHONY_WORKFLOW_PATH" not in verify
    document = (deep_dir / "document.md").read_text(encoding="utf-8")
    assert "docs/llm-wiki" in document
    assert "CHANGELOG" in document
    assert "grep -q '^verdict: GREEN'" in document
    assert "fresh verifier" in document

    intake = (deep_dir / "intake.md").read_text(encoding="utf-8")
    assert "visible control" in intake
    assert "release-contract.yaml" in intake
    qa = (deep_dir / "qa.md").read_text(encoding="utf-8")
    assert "exact target SHA" in qa
    assert "desktop, tablet, and mobile" in qa
    assert "release-evidence.json" in qa
