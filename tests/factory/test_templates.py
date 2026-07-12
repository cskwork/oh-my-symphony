from pathlib import Path
import tomllib

from symphony.workflow import build_service_config, load_workflow


ROOT = Path(__file__).resolve().parents[2]


def test_factory_workflow_is_minimal_profile() -> None:
    workflow = ROOT / "src/symphony/factory/templates/WORKFLOW.md"
    cfg = build_service_config(load_workflow(workflow))
    assert cfg.tracker.active_states == ("Ready", "Build", "Verify")
    assert cfg.tracker.terminal_states[:2] == ("Done", "Blocked")
    assert cfg.agent.auto_triage_actionable_todo is False
    text = (workflow.parent / "docs/symphony-prompts/file/base.md").read_text(encoding="utf-8")
    assert "Supergoal owns" in text
    assert len(text.split()) < 450


def test_factory_stage_prompts_cover_the_complete_owned_loop() -> None:
    prompt_root = (
        ROOT / "src/symphony/factory/templates/docs/symphony-prompts/file/stages"
    )
    ready = (prompt_root / "ready.md").read_text(encoding="utf-8")
    build = (prompt_root / "build.md").read_text(encoding="utf-8")
    verify = (prompt_root / "verify.md").read_text(encoding="utf-8")

    for anchor in ("scope", "acceptance", "proof", "route"):
        assert anchor in ready.lower()
    for anchor in ("auto-approved", "current Symphony worktree", "Improve full spec"):
        assert anchor in build
    for anchor in (
        "Intake and Wayfinder planning are complete",
        "Do not inspect `WORKFLOW.md`",
        "Begin directly with the current ticket",
        "approved scope and plan",
        "ticket is the run ledger",
        "## Full Spec Review",
        "## Edge Case Review",
        "## Adversarial Review",
    ):
        assert anchor in build
    for forbidden in (
        "create `GOAL.md`",
        "create `PLAN.md`",
        "create `QA.md`",
        "create `run-state.json`",
        "create a `Z-",
    ):
        assert forbidden not in build
    assert "Do not create separate Supergoal run-vault files" in build
    assert "Do not create any other process-evidence files" in build
    assert "docs/<ticket>/" in build
    assert "Do not read other Supergoal" in build
    assert "delivery-gate" in build
    for anchor in ("SuperQA", "REGRESSION", "report path", "side-effect", "Wayfinder ticket"):
        assert anchor in verify


def test_root_file_default_is_factory_and_advanced_profile_remains_compatible() -> None:
    cfg = build_service_config(load_workflow(ROOT / "WORKFLOW.file.example.md"))
    assert cfg.tracker.active_states == ("Ready", "Build", "Verify")
    advanced = build_service_config(
        load_workflow(ROOT / "examples/advanced/WORKFLOW.file.example.md")
    )
    assert advanced.tracker.active_states == ("Todo", "In Progress", "Verify", "Learn")


def test_factory_template_has_delivery_and_bounded_opencode_policy() -> None:
    cfg = build_service_config(
        load_workflow(ROOT / "src/symphony/factory/templates/WORKFLOW.md")
    )
    assert cfg.agent.auto_merge_on_done is True
    assert cfg.agent.max_turns == 8
    assert cfg.agent.max_total_turns == 5
    assert cfg.agent.max_state_turns_by_state == {
        "build": 3,
        "verify": 2,
    }
    assert cfg.agent.max_total_tokens == 1_250_000
    assert cfg.agent.max_total_tokens_by_state == {
        "build": 900_000,
        "verify": 350_000,
    }

    build_prompt = (
        ROOT
        / "src/symphony/factory/templates/docs/symphony-prompts/file/stages/build.md"
    ).read_text(encoding="utf-8")
    assert "Do not inventory the workspace" in build_prompt
    assert "immediately after its pass" in build_prompt


def test_factory_ready_prompt_documents_machine_dependency_gate() -> None:
    ready = (
        ROOT
        / "src/symphony/factory/templates/docs/symphony-prompts/file/stages/ready.md"
    ).read_text(encoding="utf-8")

    assert "machine dependency gate" in ready
    assert "without starting an agent" in ready


def test_factory_verify_prompt_matches_machine_table_contract() -> None:
    verify = (
        ROOT
        / "src/symphony/factory/templates/docs/symphony-prompts/file/stages/verify.md"
    ).read_text(encoding="utf-8")

    assert "Copy the complete item wording" in verify
    assert "Do not split one item into multiple rows" in verify
    assert "exactly `pass`" in verify


def test_root_advanced_workflow_remains_compatible() -> None:
    cfg = build_service_config(load_workflow(ROOT / "WORKFLOW.md"))
    assert cfg.tracker.active_states == ("Todo", "In Progress", "Verify", "Learn")


def test_runtime_template_is_packaged_with_symphony() -> None:
    from importlib.resources import files

    root = files("symphony.factory.templates")
    assert root.joinpath("WORKFLOW.md").is_file()
    assert root.joinpath("docs/symphony-prompts/file/base.md").is_file()


def test_package_data_includes_only_factory_runtime_assets() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = config["tool"]["setuptools"]
    patterns = setuptools["package-data"]["symphony"]

    assert setuptools["include-package-data"] is False
    assert "factory/templates/**/*" not in patterns
    assert "factory/templates/**/**/*" not in patterns
    assert "factory/templates/wayfinder/*.md" in patterns
    assert not list((ROOT / "src/symphony/factory/templates/wayfinder/tickets").glob("*.md"))
