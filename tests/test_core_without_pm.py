"""Core keeps working when the optional PM Workspace distribution is absent.

`oh-my-symphony-pm` (packages/pm) contributes `symphony.pm`. These tests make
that package unimportable in-process; CI also runs a core-only wheel smoke.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import cast

import pytest
from aiohttp.test_utils import TestClient, TestServer

import symphony
import symphony.orchestrator.attempt as attempt_module
from symphony.cli import main as symphony_main
from symphony.errors import ConfigValidationError
from symphony.issue import Issue, PlanningRef
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.run_registry import RunRegistry
from symphony.server import build_app
from symphony.workflow import WorkflowState
from tests.test_orchestrator_dispatch import _install_running_entry, _make_config, _orch
from tests.test_webapi import _StubOrchestrator, board_dir  # noqa: F401  (fixture)

INSTALL_COMMAND = "python -m pip install ./packages/pm"


@pytest.fixture()
def without_pm(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "symphony.pm" or name.startswith("symphony.pm."):
            monkeypatch.delitem(sys.modules, name)
    # A None entry makes both `import symphony.pm` and find_spec report absence.
    monkeypatch.setitem(sys.modules, "symphony.pm", None)
    assert not symphony.pm_installed()


def test_pm_installed_reports_the_editable_dev_install() -> None:
    assert symphony.pm_installed()


def test_symphony_pm_prints_install_hint_and_exits_2(
    without_pm: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert symphony_main(["pm", "demo"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert INSTALL_COMMAND in err


def test_symphony_pm_help_also_prints_hint(
    without_pm: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert symphony_main(["pm", "--help"]) == 2
    assert INSTALL_COMMAND in capsys.readouterr().err


async def test_web_app_starts_without_pm_routes_and_reports_availability(
    without_pm: None, board_dir: Path  # noqa: F811
) -> None:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    paths = {resource.canonical for resource in app.router.resources()}
    assert not any(path.startswith(("/api/v1/pm", "/static/pm")) for path in paths)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        board = await client.get("/api/v1/board")
        assert board.status == 200
        assert (await board.json())["board"]["pm_available"] is False
        assert (await client.get("/")).status == 200
        assert (await client.get("/api/v1/pm/capabilities")).status == 404
        assert (await client.get("/static/pm/index.js")).status == 404
    finally:
        await client.close()


async def test_web_app_serves_pm_assets_and_availability_when_installed(
    board_dir: Path,  # noqa: F811
) -> None:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    client = TestClient(TestServer(build_app(cast(Orchestrator, _StubOrchestrator(state)))))
    await client.start_server()
    try:
        assert (await (await client.get("/api/v1/board")).json())["board"]["pm_available"] is True
        asset = await client.get("/static/pm/index.js")
        assert asset.status == 200
        assert "mountArtifacts" in await asset.text()
        assert (await client.get("/static/app.js")).status == 200
    finally:
        await client.close()


def _workflow(tmp_path: Path, pm_block: str = "") -> WorkflowState:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        + pm_block
        + "---\nWork on {{ issue.identifier }}.\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir()
    return WorkflowState(path)


def test_workflow_without_pm_loads(without_pm: None, tmp_path: Path) -> None:
    cfg, err = _workflow(tmp_path).reload()
    assert err is None and cfg is not None
    assert not cfg.pm.enabled


def test_workflow_with_pm_disabled_keys_loads(without_pm: None, tmp_path: Path) -> None:
    cfg, err = _workflow(tmp_path, "pm:\n  authoring: false\n  max_nodes: 10\n").reload()
    assert err is None and cfg is not None
    assert cfg.pm.max_nodes == 10


def test_enabling_pm_without_the_package_is_a_config_error(
    without_pm: None, tmp_path: Path
) -> None:
    cfg, err = _workflow(tmp_path, "pm:\n  authoring: true\n").reload()
    assert cfg is None
    assert isinstance(err, ConfigValidationError)
    assert "pm capabilities are enabled" in str(err)
    assert INSTALL_COMMAND in str(err)


def test_pinned_planning_key_without_the_package_is_still_rejected(
    without_pm: None, tmp_path: Path
) -> None:
    cfg, err = _workflow(tmp_path, "pm:\n  planning_model: x\n").reload()
    assert cfg is None
    assert isinstance(err, ConfigValidationError)
    assert "pm.planning_model is not configurable" in str(err)


def _attempt(
    tmp_path: Path, issue: Issue, workspace_manager: object | None = None,
) -> attempt_module._AttemptState | attempt_module._AttemptExit:
    orch = _orch()
    if workspace_manager is not None:
        orch._workspace_manager = workspace_manager  # type: ignore[assignment]
    orch._run_registry = RunRegistry(tmp_path / "state.db")
    _install_running_entry(orch, issue)
    cfg = _make_config(workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws")
    try:
        return asyncio.run(attempt_module._start_attempt(orch, issue, None, cfg))
    finally:
        orch._run_registry.close()


def test_dispatch_refuses_pm_managed_work_without_the_package(
    without_pm: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        attempt_module, "_resolve_release_authority", lambda _orch, issue, _cfg: issue
    )
    issue = Issue(
        id="PM-1", identifier="PM-1", title="Managed", description=None,
        priority=None, state="Todo",
        planning_ref=PlanningRef("c1", "T-ABCDEFGH", "r1", "h1", "a1"),
    )
    result = _attempt(tmp_path, issue)
    assert isinstance(result, attempt_module._AttemptExit)
    assert result.outcome == "pm_dispatch_denied"
    assert INSTALL_COMMAND in (result.error or "")
    assert not (tmp_path / "ws").exists()


def test_dispatch_of_ordinary_work_skips_pm_authority_without_the_package(
    without_pm: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        attempt_module, "_resolve_release_authority", lambda _orch, issue, _cfg: issue
    )
    issue = Issue(
        id="ORD-1", identifier="ORD-1", title="Ordinary", description=None,
        priority=None, state="Todo",
    )

    class _Reached(Exception):
        pass

    class _Workspaces:
        async def create_or_reuse(self, _identifier: str) -> None:
            raise _Reached

    # Reaching workspace creation proves the PM authority step let it through.
    with pytest.raises(_Reached):
        _attempt(tmp_path, issue, _Workspaces())


def test_orchestrator_and_config_paths_do_not_import_pm(tmp_path: Path) -> None:
    import subprocess

    _workflow(tmp_path)
    code = (
        "import sys\n"
        "import symphony.orchestrator, symphony.webapi, symphony.cli.main\n"
        "from symphony.workflow import WorkflowState\n"
        f"cfg, err = WorkflowState({str(tmp_path / 'WORKFLOW.md')!r}).reload()\n"
        "assert err is None, err\n"
        "print(sorted(name for name in sys.modules if name.startswith('symphony.pm')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
