from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PM_ROOT = ROOT / "packages/pm"
VENDOR = PM_ROOT / "src/symphony/pm/static/vendor"


def test_exact_browser_dependency_pins_and_local_asset_inventory() -> None:
    package = json.loads((PM_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"] == {
        "dompurify": "3.4.16",
        "mermaid": "12.1.0",
        "tabulator-tables": "6.6.1",
    }
    assert package["devDependencies"] == {"esbuild": "0.25.10"}
    manifest = json.loads((VENDOR / "asset-manifest.json").read_text(encoding="utf-8"))
    assert manifest["pins"] == {
        "tabulator": "6.6.1", "mermaid": "12.1.0",
        "dompurify": "3.4.16", "esbuild": "0.25.10",
    }
    for item in manifest["files"]:
        path = VENDOR / item["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    bundled_bytes = sum(path.stat().st_size for path in VENDOR.rglob("*") if path.is_file())
    assert bundled_bytes <= 6 * 1024 * 1024


def test_diagram_distribution_excludes_non_allowlisted_named_chunks() -> None:
    names = {path.name for path in (VENDOR / "chunks").iterdir()}
    prohibited = (
        "gantt", "sequence", "classDiagram", "stateDiagram", "journey",
        "mindmap", "pieDiagram", "gitGraph", "architecture", "kanban",
    )
    assert not any(any(token in name for token in prohibited) for name in names)
    assert any(name.startswith("flowDiagram-") for name in names)
    assert any(name.startswith("erDiagram-") for name in names)


def test_notices_cover_resolved_package_identities_and_static_routes_are_lazy() -> None:
    lock = json.loads((PM_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    expected = sum(1 for key in lock["packages"] if "node_modules/" in key)
    notices = (VENDOR / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    assert len([line for line in notices.splitlines() if " — " in line and not line.startswith("PM Workspace")]) == expected
    index = (ROOT / "src/symphony/web/static/index.html").read_text(encoding="utf-8")
    assert "tabulator.min" not in index and "diagram-entry" not in index
    assert 'data-route="artifacts"' in index
