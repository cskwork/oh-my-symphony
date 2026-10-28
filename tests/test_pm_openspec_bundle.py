from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._pm_support import skip_or_fail


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "packages/pm/scripts/openspec-bundle"
BUNDLE = ROOT / "packages/pm/src/symphony/pm/resources/openspec-cli"
CLI = BUNDLE / "lib/openspec/cli/openspec.mjs"
REQUIRED = "SYMPHONY_PM_NODE_REQUIRED"


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    # Pytest itself changes this phase marker between setup and teardown.
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, monkeypatch):
    for key in tuple(os.environ):
        if key in {
            "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
            "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL",
        } or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)


@pytest.fixture
def node():
    executable = shutil.which("node")
    reason = "PM bundle tests need Node.js 20.19.0 or newer on PATH"
    if executable is None:
        skip_or_fail(f"{reason} (found: none)", flag=REQUIRED)
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        skip_or_fail(f"{reason} ({exc})", flag=REQUIRED)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    if result.returncode or not match or tuple(map(int, match.groups())) < (20, 19, 0):
        skip_or_fail(f"{reason} (found: {result.stdout.strip() or 'unknown'})", flag=REQUIRED)
    return executable


@pytest.fixture
def build_workspace(tmp_path, node):
    if not (BUILD / "node_modules/esbuild/package.json").is_file():
        skip_or_fail("PM bundle rebuild tests need npm ci --prefix packages/pm/scripts/openspec-bundle", flag=REQUIRED)
    copied = tmp_path / "repo/scripts/openspec-bundle"
    shutil.copytree(BUILD, copied)
    return copied


def run_cli(node, *args, cwd=None, cli=CLI):
    return subprocess.run(
        [node, str(cli), *args], cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def file_bytes(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*") if path.is_file()
    }


def test_d5_minimal_package_and_pinned_build_inputs():
    manifest = json.loads((BUNDLE / "bundle-manifest.json").read_text())
    assert json.loads((BUNDLE / "package.json").read_text()) == {
        "name": "@fission-ai/openspec", "version": "1.14.2", "license": "MIT", "type": "module",
    }
    assert manifest["openspec"] == "1.14.2"
    assert manifest["esbuild"] == "0.25.10"
    assert manifest["node_min"] == "20.19.0"
    package = json.loads((BUILD / "package.json").read_text())
    lock = json.loads((BUILD / "package-lock.json").read_text())
    for section, name, version in (
        ("dependencies", "@fission-ai/openspec", "1.14.2"),
        ("devDependencies", "esbuild", "0.25.10"),
    ):
        assert package[section][name] == version
        assert lock["packages"][""][section][name] == version
        assert lock["packages"][f"node_modules/{name}"]["version"] == version
    assert lock["packages"]["node_modules/commander"]["version"] == "14.0.3"


def test_r11_r12_manifest_hashes_and_exact_layout():
    manifest = json.loads((BUNDLE / "bundle-manifest.json").read_text())
    actual = file_bytes(BUNDLE)
    expected = {item["path"] for item in manifest["files"]}
    assert len(expected) == len(manifest["files"])
    assert expected == actual.keys() - {"bundle-manifest.json"}
    assert {"LICENSE", "THIRD_PARTY_NOTICES.txt", "package.json", "lib/openspec/cli/openspec.mjs"} <= expected
    assert all(
        item in {"LICENSE", "THIRD_PARTY_NOTICES.txt", "package.json", "lib/openspec/cli/openspec.mjs"}
        or item.startswith("schemas/spec-driven/") for item in expected
    )
    for item in manifest["files"]:
        assert not Path(item["path"]).is_absolute()
        assert ".." not in Path(item["path"]).parts
        data = actual[item["path"]]
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]


def test_r12_notices_and_no_telemetry():
    manifest = json.loads((BUNDLE / "bundle-manifest.json").read_text())
    notices = (BUNDLE / "THIRD_PARTY_NOTICES.txt").read_text()
    packages = manifest["packages"]
    assert len({(item["name"], item["version"]) for item in packages}) == len(packages)
    assert {item["license"] for item in packages} == {"MIT", "ISC"}
    assert {"name": "@fission-ai/openspec", "version": "1.14.2", "license": "MIT"} in packages
    for item in packages:
        identity = f"{item['name']} {item['version']} — {item['license']}\n\n"
        assert notices.count(identity) == 1
        section = notices.split(identity, 1)[1].split("\n\n\n", 1)[0]
        assert "Permission" in section
        assert "copyright" in section.lower()
        assert not re.search("posthog|telemetry", item["name"], re.I)
    assert (BUNDLE / "LICENSE").read_text() in notices
    assert not re.search("posthog|telemetry", CLI.read_text(), re.I)


def test_r11_relative_imports_and_no_build_machine_paths():
    entry = (BUILD / "entry.mjs").read_text()
    imports = re.findall(r"from ['\"]([^'\"]+)['\"]", entry)
    assert imports
    assert all(item.startswith("./") or item in {"commander", "node:module"} for item in imports)
    for text in (entry, CLI.read_text(), (BUNDLE / "bundle-manifest.json").read_text()):
        assert str(ROOT) not in text
        assert not re.search(r"/(?:Users|private|home)/|(?<![\w])[A-Za-z]:[\\/]|\.sdlc/|scratch/", text)


def test_r18_builtin_schema_file_layout():
    assert CLI.is_file()
    assert CLI.parent.parent.parent.parent == BUNDLE
    assert (CLI.parent.parent.parent.parent / "schemas/spec-driven/schema.yaml").is_file()


def test_r11_checkout_preserves_hashes_and_ignores_build_dependencies():
    assert "packages/pm/scripts/openspec-bundle/node_modules/" in (ROOT / ".gitignore").read_text().splitlines()
    assert (
        "packages/pm/src/symphony/pm/resources/openspec-cli/** -text -whitespace linguist-generated"
        in (ROOT / ".gitattributes").read_text().splitlines()
    )


def test_d5_version(node):
    result = run_cli(node, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1.14.2\n"


@pytest.mark.parametrize(("args", "expected"), [
    (("--help",), ["schema", "status", "instructions", "validate", "archive", "help"]),
    (("schema", "--help"), ["validate", "help"]),
])
def test_r18_exact_commands(node, args, expected):
    result = run_cli(node, *args)
    assert result.returncode == 0, result.stderr
    commands = result.stdout.split("Commands:\n", 1)[1]
    assert re.findall(r"^  (\w+)", commands, re.M) == expected


@pytest.mark.parametrize(("args", "expected"), [
    (("--help",), {"-V", "--version", "-h", "--help"}),
    (("schema", "--help"), {"-h", "--help"}),
    (("schema", "validate", "--help"), {"--json", "--verbose", "-h", "--help"}),
    (("status", "--help"), {"--change", "--json", "-h", "--help"}),
    (("instructions", "--help"), {"--change", "--json", "-h", "--help"}),
    (("validate", "--help"), {"--type", "--strict", "--json", "--no-interactive", "-h", "--help"}),
    (("archive", "--help"), {"-y", "--yes", "--json", "-h", "--help"}),
])
def test_r18_exact_options(node, args, expected):
    result = run_cli(node, *args)
    assert result.returncode == 0, result.stderr
    options = result.stdout.split("Options:\n", 1)[1].split("\nCommands:", 1)[0]
    assert set(re.findall(r"(?<!\w)--?[\w-]+", options)) == expected


@pytest.mark.parametrize("args", [
    ("schema", "which"), ("schema", "fork"), ("schema", "init"), ("init",), ("update",),
])
def test_r18_forbidden_commands(node, args):
    result = run_cli(node, *args)
    assert result.returncode != 0
    assert "unknown command" in result.stderr


@pytest.mark.parametrize("args", [
    ("status", "--all"), ("status", "--schema", "spec-driven"),
    ("instructions", "apply", "--schema", "spec-driven"),
    *(("validate", "x", option) for option in (
        "--all", "--changes", "--specs", "--archived", "--concurrency", "--report",
    )),
    ("archive", "x", "--skip-specs"), ("archive", "x", "--no-validate"),
    ("--store", "x"), ("--no-color",),
])
def test_r18_forbidden_options(node, args):
    result = run_cli(node, *args)
    assert result.returncode != 0
    assert "unknown option" in result.stderr


def test_r18_only_apply_instructions(node):
    result = run_cli(node, "instructions", "proposal")
    assert result.returncode != 0
    assert "Allowed choices are apply." in result.stderr


def test_r18_status_uses_builtin_schema(node, tmp_path):
    project = tmp_path / "planning"
    change = project / "openspec/changes/add-greeting"
    change.mkdir(parents=True)
    (project / "openspec/config.yaml").write_text("schema: spec-driven\n")
    (change / ".openspec.yaml").write_text("schema: spec-driven\ncreated: '2026-10-11'\n")
    (change / "proposal.md").write_text("## Why\n\nGreet visitors.\n\n## What Changes\n\nAdd a greeting.\n")
    result = run_cli(node, "status", "--change", "add-greeting", "--json", cwd=project)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schemaName"] == "spec-driven"
    # Control: the same CLI without its schemas must fail, proving the layout matters.
    missing = tmp_path / "no-schemas"
    shutil.copytree(BUNDLE / "lib", missing / "lib")
    shutil.copyfile(BUNDLE / "package.json", missing / "package.json")
    result = run_cli(node, "status", "--change", "add-greeting", "--json", cwd=project,
                     cli=missing / "lib/openspec/cli/openspec.mjs")
    assert result.returncode != 0
    assert "Unknown schema 'spec-driven'" in result.stdout + result.stderr


@pytest.mark.parametrize("nesting", ["{,", "{"])
@pytest.mark.parametrize(("field", "command"), [
    ("generates", "status"),
    ("generates", "instructions"),
    ("tracks", "instructions"),
])
def test_deeply_nested_schema_glob_is_refused_not_crashed(
    node, tmp_path, field, command, nesting,
):
    # braces <=3.0.3 (GHSA-vfj7-8cjw-p6xm) overflows the stack on both shapes
    # below its 10,000-character limit. OpenSpec must refuse the pattern (or
    # treat it as a literal path) before braces walks it.
    depth = 9_980 // (len(nesting) + 1)
    pattern = "{" + nesting * depth + "a" + "}" * depth + "}"
    schema = (BUNDLE / "schemas/spec-driven/schema.yaml").read_text()
    schema = schema.replace("name: spec-driven", "name: deep", 1)
    original = {"generates": "generates: proposal.md", "tracks": "tracks: tasks.md"}[field]
    assert original in schema
    schema = schema.replace(original, f"{field}: '{pattern}'", 1)
    project = tmp_path / "planning"
    schema_dir = project / "openspec/schemas/deep"
    shutil.copytree(BUNDLE / "schemas/spec-driven/templates", schema_dir / "templates")
    (schema_dir / "schema.yaml").write_text(schema)
    (project / "openspec/config.yaml").write_text("schema: deep\n")
    change = project / "openspec/changes/add-greeting"
    change.mkdir(parents=True)
    (change / ".openspec.yaml").write_text("schema: deep\ncreated: '2026-10-16'\n")
    args = ("status",) if command == "status" else ("instructions", "apply")
    result = run_cli(node, *args, "--change", "add-greeting", "--json", cwd=project)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["status"][0]["severity"] == "error"
    if "," in nesting:
        assert "nests braces more than 16 levels deep" in result.stdout
    assert "RangeError" not in result.stdout + result.stderr


def test_bundled_braces_has_depth_guard():
    assert "Brace pattern nests more than" in CLI.read_text()


def test_braces_stub_refuses_deep_nesting_and_keeps_normal_expansion(node):
    if not (BUILD / "node_modules/braces/package.json").is_file():
        skip_or_fail("braces stub test needs npm ci --prefix packages/pm/scripts/openspec-bundle", flag=REQUIRED)
    script = r"""
const real = require('./node_modules/braces/index.js');
const guarded = require('./stubs/braces.cjs');
const run = (f) => { try { return f(); } catch (e) { return e.constructor.name + ': ' + e.message; } };
const deep = (unit, depth) => '{' + unit.repeat(depth) + 'a' + '}'.repeat(depth) + '}';
console.log(JSON.stringify({
  crashOpen: run(() => real(deep('{', 4990))).split(':')[0],
  crashComma: run(() => real(deep('{,', 3320))).split(':')[0],
  open: run(() => guarded(deep('{', 4990))),
  comma: run(() => guarded.expand(deep('{,', 3320))),
  limit: run(() => guarded('{'.repeat(16) + 'a,b' + '}'.repeat(16)).length),
  over: run(() => guarded.parse('{'.repeat(17) + 'a,b' + '}'.repeat(17))),
  escaped: run(() => guarded('\\{'.repeat(40) + '{a,b}').length),
  normal: guarded.expand('specs/{a,b}/*.{md,txt}'),
  reference: real.expand('specs/{a,b}/*.{md,txt}'),
}));
"""
    result = subprocess.run([node, "-e", script], cwd=BUILD, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    refused = "Error: Brace pattern nests more than 16 levels"
    assert outcome["crashOpen"] == outcome["crashComma"] == "RangeError"
    assert outcome["open"] == outcome["comma"] == outcome["over"] == refused
    assert outcome["limit"] == 1
    assert outcome["escaped"] == 1
    assert outcome["normal"] == outcome["reference"] == [
        "specs/a/*.md", "specs/a/*.txt", "specs/b/*.md", "specs/b/*.txt",
    ]


def test_r11_rebuild_is_byte_identical(node, build_workspace, tmp_path):
    output = build_workspace.parents[1] / "src/symphony/pm/resources/openspec-cli"
    for _ in range(2):
        result = subprocess.run([node, str(build_workspace / "build.mjs")], cwd=tmp_path,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert file_bytes(output) == file_bytes(BUNDLE)
    manifest = json.loads((output / "bundle-manifest.json").read_text())
    notices = (output / "THIRD_PARTY_NOTICES.txt").read_text()
    for item in manifest["packages"]:
        package = build_workspace / "node_modules" / item["name"]
        for file in package.iterdir():
            if file.is_file() and re.match(r"^(licen[cs]e|copying)", file.name, re.I):
                assert file.read_text() in notices


@pytest.mark.parametrize("broken", ["commander", "absolute-import", "license"])
def test_r11_r12_invalid_build_inputs_fail(node, build_workspace, tmp_path, broken):
    if broken == "commander":
        package = build_workspace / "node_modules/commander/package.json"
        data = json.loads(package.read_text())
        data["version"] = "7.2.0"
        package.write_text(json.dumps(data))
        expected = "Build requires commander 14.0.3"
    elif broken == "absolute-import":
        module = tmp_path / "absolute.mjs"
        module.write_text("console.log('absolute import');\n")
        entry = build_workspace / "entry.mjs"
        entry.write_text(f"import {json.dumps(str(module))};\n" + entry.read_text())
        expected = "Absolute import is forbidden"
    else:
        (build_workspace / "node_modules/chalk/license").unlink()
        expected = "Missing license file: chalk"
    output = build_workspace.parents[1] / "src/symphony/pm/resources/openspec-cli"
    output.mkdir(parents=True)
    (output / "sentinel").write_text("previous build")
    result = subprocess.run([node, str(build_workspace / "build.mjs")], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert expected in result.stderr
    assert file_bytes(output) == {"sentinel": b"previous build"}


def test_p5_skip_or_fail(monkeypatch):
    flag = "SYMPHONY_PM_EQUIVALENCE_REQUIRED"
    monkeypatch.delenv(flag, raising=False)
    with pytest.raises(pytest.skip.Exception, match="missing tool"):
        skip_or_fail("missing tool", flag=flag)
    monkeypatch.setenv(flag, "1")
    with pytest.raises(pytest.fail.Exception, match="missing tool"):
        skip_or_fail("missing tool", flag=flag)
