from importlib.resources import files
import json
from pathlib import Path
import re

import pytest

from symphony.cli import factory as factory_cli


STANDARD_SKILLS = ("supergoal", "superdesign", "superpm", "superqa")
REFERENCED_ASSET_EXTENSIONS = {
    ".cmd",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".tcss",
    ".toml",
}
RUNTIME_CLOSURE = (
    "supergoal/reference/role-loop.md",
    "supergoal/agents/executor.md",
    "superpm/reference/intent.md",
    "superpm/reference/signal.md",
    "superpm/reference/research.md",
    "superpm/reference/execute.md",
    "superpm/reference/critic.md",
    "superpm/scripts/shoot.sh",
    "superdesign/agents/designer.md",
    "superdesign/reference/sources.md",
    "superdesign/templates/preflight-gate.sh",
    "superdesign/templates/anti-slop-gate.mjs",
    "superdesign/templates/contrast-gate.mjs",
    "superqa/pyproject.toml",
    "superqa/scripts/superqa.sh",
    "superqa/reference/scenario-gen.md",
    "superqa/reference/side-effects.md",
    "superqa/reference/report.md",
    "superqa/superqa_tui/engine.py",
    "superqa/superqa_tui/recorder_overlay.js",
    "superqa/superqa_tui/superqa.tcss",
)
REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:agents|reference|scripts|templates|superqa_tui)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
)


def _missing_runtime_references(root: Path) -> list[str]:
    pending = [root / "SKILL.md"]
    visited: set[Path] = set()
    missing: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        text = current.read_text(encoding="utf-8")
        for match in REFERENCE_RE.finditer(text):
            relative = Path(match.group(1))
            referenced = root / relative
            if (
                not referenced.exists()
                and relative.suffix in REFERENCED_ASSET_EXTENSIONS
            ):
                missing.add(relative.as_posix())
            elif (
                referenced.is_file()
                and referenced.suffix in REFERENCED_ASSET_EXTENSIONS
            ):
                pending.append(referenced)
    return sorted(missing)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        factory_cli,
        "_SKILL_SEARCH_ROOTS",
        (home / ".agents/skills", home / ".codex/skills"),
    )
    return home


def test_factory_init_uses_bundled_supergoal_with_empty_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    target = tmp_path / "project"

    assert factory_cli.main(["init", str(target)]) == 0

    assert (target / "skills/supergoal/SKILL.md").is_file()


def test_standard_overlay_install_uses_bundle_with_empty_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    target = tmp_path / "project"

    factory_cli._install_skills(target, set(STANDARD_SKILLS), force=False)

    for name in STANDARD_SKILLS:
        assert (target / "skills" / name / "SKILL.md").is_file()


def test_existing_project_skill_without_entrypoint_is_not_treated_as_valid(
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    incomplete = target / "skills/superqa/reference"
    incomplete.mkdir(parents=True)
    (incomplete / "agent-qa.md").write_text("runtime support\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="factory sync.*--force"):
        factory_cli._install_skills(target, {"superqa"}, force=False)


@pytest.mark.parametrize("destination_kind", ["file", "symlink", "broken-symlink"])
def test_force_replaces_non_directory_project_skill_destinations(
    tmp_path: Path, destination_kind: str
) -> None:
    target = tmp_path / "project"
    destination = target / "skills/supergoal"
    destination.parent.mkdir(parents=True)
    if destination_kind == "file":
        destination.write_text("custom file\n", encoding="utf-8")
    else:
        link_target = tmp_path / (
            "custom-supergoal" if destination_kind == "symlink" else "missing-supergoal"
        )
        if destination_kind == "symlink":
            (link_target / "reference").mkdir(parents=True)
            (link_target / "agents").mkdir()
            (link_target / "SKILL.md").write_text("custom\n", encoding="utf-8")
            (link_target / "reference/role-loop.md").write_text(
                "custom loop\n", encoding="utf-8"
            )
            (link_target / "agents/executor.md").write_text(
                "custom executor\n", encoding="utf-8"
            )
        destination.symlink_to(link_target, target_is_directory=True)

    factory_cli._install_skills(target, {"supergoal"}, force=True)

    assert destination.is_dir()
    assert not destination.is_symlink()
    assert "custom" not in (destination / "SKILL.md").read_text(encoding="utf-8")
    if destination_kind == "symlink":
        assert (link_target / "SKILL.md").read_text(encoding="utf-8") == "custom\n"


def test_broken_project_skill_symlink_requires_force(tmp_path: Path) -> None:
    target = tmp_path / "project"
    destination = target / "skills/supergoal"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-supergoal", target_is_directory=True)

    with pytest.raises(FileExistsError, match="factory sync.*--force"):
        factory_cli._install_skills(target, {"supergoal"}, force=False)


def test_init_preflights_broken_skill_symlink_before_copying_assets(
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    destination = target / "skills/supergoal"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-supergoal", target_is_directory=True)

    assert factory_cli.main(["init", str(target)]) == 1

    assert destination.is_symlink()
    assert not (target / "WORKFLOW.md").exists()


def test_bundled_standard_skills_are_package_resources() -> None:
    root = files("symphony.factory.bundled_skills")

    for name in STANDARD_SKILLS:
        assert root.joinpath(name, "SKILL.md").is_file()
        assert root.joinpath(name, "LICENSE").is_file()
    for relative in RUNTIME_CLOSURE:
        assert root.joinpath(*Path(relative).parts).is_file()
    assert root.joinpath("MANIFEST.json").is_file()


def test_third_party_notices_cover_pinned_sources_and_legal_uncertainty() -> None:
    root = files("symphony.factory.bundled_skills")
    notices = root.joinpath("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    for name in STANDARD_SKILLS:
        assert f"{name}/LICENSE" in notices
    assert "Residual legal uncertainty" in notices
    assert "not legal approval" in notices


def test_third_party_inventory_covers_adapted_sources_and_license_texts() -> None:
    root = files("symphony.factory.bundled_skills")
    notices = root.joinpath("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    expected = {
        "taste-skill": ("third_party_licenses/taste-skill-MIT.txt", "Leonxlnx"),
        "impeccable": ("third_party_licenses/impeccable-Apache-2.0.txt", "Paul Bakaus"),
        "last30days-skill": (
            "third_party_licenses/last30days-skill-MIT.txt",
            "Matt Van Horn",
        ),
        "Agent-Reach": ("third_party_licenses/Agent-Reach-MIT.txt", "Agent Eyes"),
        "storyboard-spec": (
            "third_party_licenses/storyboard-spec-MIT.txt",
            "cskwork",
        ),
        "stitch-landing-skill": (
            "third_party_licenses/stitch-landing-skill-MIT.txt",
            "cskwork",
        ),
    }

    for source, (relative, copyright_holder) in expected.items():
        assert source in notices
        assert relative in notices
        license_text = root.joinpath(*Path(relative).parts).read_text(encoding="utf-8")
        assert copyright_holder in license_text
    manifest = json.loads(root.joinpath("MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["adapted_sources"]["stitch-landing-skill"] == {
        "source": "https://github.com/cskwork/stitch-landing-skill",
        "commit": "4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c",
    }

    sources = root.joinpath("superdesign/reference/sources.md").read_text(
        encoding="utf-8"
    )
    assert "inspiration for `assets.md` and `web.md`" in sources
    assert "GH Pages landing" not in sources
    assert "stitch-landing-skill license could not be independently fetched" not in notices


def test_all_source_and_generated_shell_assets_are_executable(tmp_path: Path) -> None:
    bundled = Path(str(files("symphony.factory.bundled_skills")))
    target = tmp_path / "project"
    factory_cli._install_skills(target, set(STANDARD_SKILLS), force=False)

    for source in bundled.rglob("*.sh"):
        relative = source.relative_to(bundled)
        generated = target / "skills" / relative
        assert source.stat().st_mode & 0o111, f"source is not executable: {relative}"
        assert generated.stat().st_mode & 0o111, f"generated is not executable: {relative}"


def test_superqa_editable_install_metadata_has_its_readme() -> None:
    root = files("symphony.factory.bundled_skills").joinpath("superqa")

    assert root.joinpath("README.md").is_file()


@pytest.mark.parametrize(
    "path",
    [
        Path("README.md"),
        Path("README.ko.md"),
        Path("skills/symphony-skill/reference/factory.md"),
    ],
)
def test_beginner_docs_name_superdesign_runtime_prerequisites(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert "Node.js 18+" in text
    assert "@playwright/cli" in text
    assert "playwright-cli install --skills" in text


def test_bundled_and_generated_standard_skills_have_recursive_reference_closure(
    tmp_path: Path,
) -> None:
    bundled = Path(str(files("symphony.factory.bundled_skills")))
    target = tmp_path / "project"
    factory_cli._install_skills(target, set(STANDARD_SKILLS), force=False)

    for name in STANDARD_SKILLS:
        assert _missing_runtime_references(bundled / name) == []
        assert _missing_runtime_references(target / "skills" / name) == []


@pytest.mark.parametrize("suffix", sorted(REFERENCED_ASSET_EXTENSIONS))
def test_recursive_reference_scan_covers_every_shipped_text_asset_extension(
    tmp_path: Path, suffix: str
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(f"Read templates/missing{suffix}\n", encoding="utf-8")

    assert _missing_runtime_references(root) == [f"templates/missing{suffix}"]


def test_existing_standard_skill_requires_complete_pinned_runtime(
    tmp_path: Path,
) -> None:
    target = tmp_path / "project"
    factory_cli._install_skills(target, {"superpm"}, force=False)
    (target / "skills/superpm/reference/strategy.md").unlink()

    with pytest.raises(FileExistsError, match="factory sync.*--force"):
        factory_cli._install_skills(target, {"superpm"}, force=False)


def test_custom_skill_still_falls_back_to_local_search_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    custom = home / ".agents/skills/custom-check"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text("# Custom check\n", encoding="utf-8")
    target = tmp_path / "project"

    factory_cli._install_skills(target, {"custom-check"}, force=False)

    assert (target / "skills/custom-check/SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Custom check\n"


def test_unknown_custom_skill_has_actionable_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _isolate_home(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError) as exc_info:
        factory_cli._skill_sources({"missing-check"})

    message = str(exc_info.value)
    assert "missing-check" in message
    assert str(home / ".agents/skills") in message
