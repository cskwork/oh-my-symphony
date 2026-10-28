"""R14: compare every product call with an unbundled OpenSpec 1.14.2 CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from symphony.pm import demo, openspec
from symphony.pm.models import PmError
from tests._pm_support import skip_or_fail


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI = ROOT / "packages/pm/scripts/openspec-bundle/node_modules/.bin/openspec"
REQUIRED = "SYMPHONY_PM_EQUIVALENCE_REQUIRED"


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in {
            "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
            "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL",
        } or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})


def run_cli(command: list[str], root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command, *args], cwd=root, capture_output=True, text=True, timeout=30,
        env={**os.environ, "OPENSPEC_TELEMETRY": "0"},
    )


@pytest.fixture
def commands(tmp_path):
    override = os.environ.get("SYMPHONY_PM_OPENSPEC")
    executable = shutil.which(override if override is not None else str(DEFAULT_CLI))
    if executable is None:
        reason = f"R14 needs an executable OpenSpec 1.14.2 CLI: {override or DEFAULT_CLI}"
        if override is not None:
            pytest.fail(reason + " (SYMPHONY_PM_OPENSPEC is set; no fallback)")
        skip_or_fail(reason + "; run npm ci --prefix packages/pm/scripts/openspec-bundle", flag=REQUIRED)
    try:
        node, _ = openspec.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag=REQUIRED)
    if shutil.which("git") is None:
        skip_or_fail("R14 demo fixtures need git on PATH", flag=REQUIRED)
    bundled = [node, str(openspec.BUNDLE_CLI)]
    reference = [executable]
    for command in (bundled, reference):
        result = run_cli(command, tmp_path, "--version")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "1.14.2\n"
    return bundled, reference


def normalise(stdout: str, root: Path) -> str:
    stdout = stdout.replace(str(root), "<ROOT>")
    return re.sub(r'("durationMs"\s*:\s*)\d+', r'\g<1>0', stdout)


def tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("fixture", ["en", "ko", "no-scenario"])
def test_r14_product_calls_and_archive_trees_are_equivalent(tmp_path, commands, fixture):
    inputs = demo.preflight_demo(tmp_path / "demo", "ko" if fixture == "ko" else "en")
    directory = demo.claim_demo_directory(inputs)
    workspace = demo.create_demo(directory, inputs.lang)
    change = Path("openspec") / "changes" / demo.LOCATOR
    if fixture == "no-scenario":
        (workspace.planning_path / change / "specs/requirements/spec.md").write_text(
            "## ADDED Requirements\n\n### Requirement: Leave request\n\n"
            "The system SHALL store a leave request.\n", encoding="utf-8",
        )
    roots = (tmp_path / "bundled planning", tmp_path / "reference planning")
    for root in roots:
        shutil.copytree(workspace.planning_path / "openspec", root / "openspec")
    before = tree_bytes(roots[0] / "openspec")
    assert before == tree_bytes(roots[1] / "openspec")
    invalid = fixture == "no-scenario"
    # These are the six argv shapes in OpenSpecAdapter; `show` is not a product call.
    calls = (
        ("--version",),
        ("schema", "validate", "symphony-pm", "--json"),
        ("status", "--change", demo.LOCATOR, "--json"),
        ("instructions", "apply", "--change", demo.LOCATOR, "--json"),
        ("validate", demo.LOCATOR, "--type", "change", "--strict", "--json", "--no-interactive"),
        ("archive", demo.LOCATOR, "--yes", "--json"),
    )
    for args in calls:
        bundled, reference = (
            run_cli(command, root, *args) for command, root in zip(commands, roots, strict=True)
        )
        assert bundled.returncode == reference.returncode, (args, bundled, reference)
        assert normalise(bundled.stdout, roots[0]) == normalise(reference.stdout, roots[1]), args
        expected = 1 if invalid and args[0] in {"validate", "archive"} else 0
        assert bundled.returncode == expected, (args, bundled.stdout, bundled.stderr)
        if args[0] == "validate":
            item = json.loads(bundled.stdout)["items"][0]
            assert item["valid"] is not invalid
            if invalid:
                assert any("scenario" in issue["message"].lower() for issue in item["issues"])
        if args[0] == "archive":
            payload = json.loads(bundled.stdout)
            if invalid:
                assert tree_bytes(roots[0] / "openspec") == before
            else:
                archived = Path(payload["archive"]["path"])
                assert archived.is_dir()
                assert archived.parent == roots[0] / "openspec/changes/archive"
                assert not (roots[0] / change).exists()
                assert (roots[0] / "openspec/specs/requirements/spec.md").is_file()
                assert (roots[0] / "openspec/specs/team-calendar/spec.md").is_file()
    assert tree_bytes(roots[0] / "openspec") == tree_bytes(roots[1] / "openspec")


LEAVE_DESCRIPTION_500 = (
    "The system SHALL record each leave request with its employee, requested dates, "
    "leave type, and current status. It SHALL show the request in the employee's leave "
    "history after saving, so the employee can confirm the dates and review its status. "
    "The record SHALL retain the employee's note and the manager's decision with their "
    "timestamps. Authorized managers SHALL see the same saved details when reviewing "
    "the request. Employees SHALL see a clear confirmation after the request is saved "
    "successfully."
)
LEAVE_DESCRIPTION_501 = LEAVE_DESCRIPTION_500.replace(
    "The record SHALL", "Each record SHALL",
)
LEAVE_REVIEW_NOTE = "Managers use this record during the weekly staffing review."
LEAVE_PURPOSE = (
    "Keep a durable leave record so an employee and manager can review the same "
    "saved details."
)
LEAVE_SCENARIO = (
    "#### Scenario: Employee reviews a saved request\n"
    "- **WHEN** the employee submits a valid leave request\n"
    "- **THEN** the system shows the saved employee, dates, and request status\n"
)


@pytest.mark.parametrize(
    ("description", "section", "outside_note", "warning_fragment"),
    [
        pytest.param(LEAVE_DESCRIPTION_500, "ADDED", False, None, id="added-500"),
        pytest.param(
            LEAVE_DESCRIPTION_501, "ADDED", False,
            "Requirement text is very long", id="added-501",
        ),
        pytest.param(LEAVE_DESCRIPTION_501, "MODIFIED", False, None, id="modified-501"),
        pytest.param(
            "The system SHALL save each leave request.", "ADDED", True,
            "not carried into the main spec", id="explanation-outside-requirement",
        ),
        pytest.param(
            "The system SHALL save each leave request. " + LEAVE_REVIEW_NOTE,
            "ADDED", False, None, id="explanation-inside-requirement",
        ),
    ],
)
def test_r14_approved_1142_policy_matches_reference_and_adapter(
    tmp_path, commands, description, section, outside_note, warning_fragment,
):
    assert LEAVE_DESCRIPTION_500.isascii() and len(LEAVE_DESCRIPTION_500) == 500
    assert LEAVE_DESCRIPTION_501.isascii() and len(LEAVE_DESCRIPTION_501) == 501
    inputs = demo.preflight_demo(tmp_path / "demo", "en")
    directory = demo.claim_demo_directory(inputs)
    workspace = demo.create_demo(directory, inputs.lang)
    change = Path("openspec") / "changes" / demo.LOCATOR
    roots = (tmp_path / "bundled planning", tmp_path / "reference planning")
    requirement_header = "### Requirement: Save leave request details\n\n"
    for root in roots:
        shutil.copytree(workspace.planning_path / "openspec", root / "openspec")
        if section == "MODIFIED":
            main_spec = root / "openspec/specs/requirements/spec.md"
            main_spec.parent.mkdir(parents=True, exist_ok=True)
            main_spec.write_text(
                "# Leave request records\n\n## Purpose\n\n" + LEAVE_PURPOSE
                + "\n\n## Requirements\n\n" + requirement_header
                + "The system SHALL store the employee and requested dates.\n\n"
                + LEAVE_SCENARIO, encoding="utf-8",
            )
        (root / change / "specs/requirements/spec.md").write_text(
            "# Leave request records\n\n"
            + (LEAVE_REVIEW_NOTE + "\n\n" if outside_note else "")
            + "## " + section + " Requirements\n\n"
            + requirement_header + description + "\n\n" + LEAVE_SCENARIO,
            encoding="utf-8",
        )
    before = tuple(tree_bytes(root / "openspec") for root in roots)
    assert before[0] == before[1]

    for strict in (False, True):
        args = (
            "validate", demo.LOCATOR, "--type", "change",
            *(("--strict",) if strict else ()), "--json", "--no-interactive",
        )
        results = tuple(
            run_cli(command, root, *args)
            for command, root in zip(commands, roots, strict=True)
        )
        assert results[0].returncode == results[1].returncode
        assert normalise(results[0].stdout, roots[0]) == normalise(
            results[1].stdout, roots[1],
        )
        valid = not (strict and warning_fragment is not None)
        for result in results:
            assert result.returncode == (0 if valid else 1), (args, result.stdout, result.stderr)
            item = json.loads(result.stdout)["items"][0]
            assert item["id"] == demo.LOCATOR and item["type"] == "change"
            assert item["valid"] is valid
            assert not any(issue["level"] == "ERROR" for issue in item["issues"])
            warnings = [
                issue["message"] for issue in item["issues"] if issue["level"] == "WARNING"
            ]
            if warning_fragment is None:
                assert warnings == []
            else:
                assert len(warnings) == 1 and warning_fragment in warnings[0]

    for index, root in enumerate(roots):
        adapter = openspec.OpenSpecAdapter(None if index == 0 else commands[1][0])
        report = adapter.validate(root, demo.LOCATOR)
        assert report.valid is (warning_fragment is None)
        if warning_fragment is None:
            assert all(finding.severity != "blocking" for finding in report.findings)
        else:
            assert report.findings
            assert all(finding.severity == "blocking" for finding in report.findings)
            assert any(warning_fragment in finding.message for finding in report.findings)
    assert tuple(tree_bytes(root / "openspec") for root in roots) == before



@pytest.mark.parametrize(
    ("metadata_key", "valid"),
    [pytest.param("x-tracker", True, id="extension-key"),
     pytest.param("tracker", False, id="unknown-key")],
)
def test_r14_1142_metadata_key_policy_matches_reference(
    tmp_path, commands, metadata_key, valid,
):
    inputs = demo.preflight_demo(tmp_path / "demo", "en")
    directory = demo.claim_demo_directory(inputs)
    workspace = demo.create_demo(directory, inputs.lang)
    roots = (tmp_path / "bundled planning", tmp_path / "reference planning")
    for root in roots:
        shutil.copytree(workspace.planning_path / "openspec", root / "openspec")
        metadata = root / "openspec/changes" / demo.LOCATOR / ".openspec.yaml"
        source = metadata.read_text(encoding="utf-8")
        assert not re.search(r"(?m)^" + re.escape(metadata_key) + r":", source)
        metadata.write_text(
            source.rstrip() + "\n" + metadata_key + ": leave-review-42\n",
            encoding="utf-8",
        )
    before = tuple(tree_bytes(root / "openspec") for root in roots)
    assert before[0] == before[1]
    args = (
        "validate", demo.LOCATOR, "--type", "change", "--strict",
        "--json", "--no-interactive",
    )
    results = tuple(
        run_cli(command, root, *args)
        for command, root in zip(commands, roots, strict=True)
    )
    assert results[0].returncode == results[1].returncode
    assert normalise(results[0].stdout, roots[0]) == normalise(results[1].stdout, roots[1])
    for result in results:
        assert result.returncode == (0 if valid else 1), (result.stdout, result.stderr)
        item = json.loads(result.stdout)["items"][0]
        assert item["id"] == demo.LOCATOR and item["type"] == "change"
        assert item["valid"] is valid
        assert not any(issue["level"] == "ERROR" for issue in item["issues"])
        warnings = [
            issue["message"] for issue in item["issues"] if issue["level"] == "WARNING"
        ]
        if valid:
            assert warnings == []
        else:
            assert len(warnings) == 1
            assert "Unrecognized key name(s)" in warnings[0] and metadata_key in warnings[0]
    assert tuple(tree_bytes(root / "openspec") for root in roots) == before


def test_r14_1142_completed_tasks_preserve_review_instruction(tmp_path, commands):
    inputs = demo.preflight_demo(tmp_path / "demo", "en")
    directory = demo.claim_demo_directory(inputs)
    workspace = demo.create_demo(directory, inputs.lang)
    roots = (tmp_path / "bundled planning", tmp_path / "reference planning")
    totals = []
    for root in roots:
        shutil.copytree(workspace.planning_path / "openspec", root / "openspec")
        tasks = root / "openspec/changes" / demo.LOCATOR / "tasks.md"
        source = tasks.read_text(encoding="utf-8")
        updated, changed = re.subn(
            r"(?m)^([ \t]*-[ \t]+)\[ \](?=[ \t])", r"\1[x]", source,
        )
        assert changed > 0
        tasks.write_text(updated, encoding="utf-8")
        totals.append(len(re.findall(r"(?m)^[ \t]*-[ \t]+\[[xX]\](?=[ \t])", updated)))
    assert totals[0] == totals[1] and totals[0] > 0
    before = tuple(tree_bytes(root / "openspec") for root in roots)
    assert before[0] == before[1]
    args = ("instructions", "apply", "--change", demo.LOCATOR, "--json")
    results = tuple(
        run_cli(command, root, *args)
        for command, root in zip(commands, roots, strict=True)
    )
    assert results[0].returncode == results[1].returncode
    assert normalise(results[0].stdout, roots[0]) == normalise(results[1].stdout, roots[1])
    for result in results:
        assert result.returncode == 0, (result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["state"] == "all_done"
        assert payload["progress"]["total"] == totals[0]
        assert payload["progress"]["complete"] == totals[0]
        assert payload["progress"]["remaining"] == 0
        assert payload["instruction"] == (
            "All tracked tasks are complete.\n"
            "Review or verify the change as appropriate before archiving."
        )
    assert tuple(tree_bytes(root / "openspec") for root in roots) == before
