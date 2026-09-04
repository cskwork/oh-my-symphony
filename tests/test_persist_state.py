"""Regression coverage for `.symphony/` state persistence (#30, #32).

#30: on Windows a concurrent reader holding the target open makes the rename
fail with ``PermissionError``; the writer retries a couple of times before
giving up.

#32: two orchestrators whose workflow files share one directory used to
overwrite each other's ``token_ema.json`` / ``done_count.json``; the record
name is now namespaced by the workflow file name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from symphony.orchestrator.core import Orchestrator
from symphony.utils import atomic_json
from symphony.utils.atomic_json import state_file_name, write_json_atomic


def _leftover_tmp(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.suffix == ".tmp"]


def test_write_json_atomic_retries_rename_held_by_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state" / "token_ema.json"
    real_replace = os.replace
    failures = {"left": 2}
    sleeps: list[float] = []

    def flaky_replace(src: str, dst: str) -> None:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(32, "The process cannot access the file")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_json.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic_json.time, "sleep", sleeps.append)

    write_json_atomic(target, {"in progress": 1234.5})

    assert json.loads(target.read_text(encoding="utf-8")) == {"in progress": 1234.5}
    assert failures["left"] == 0
    assert sleeps == [0.01, 0.05]
    assert _leftover_tmp(target.parent) == []


def test_write_json_atomic_gives_up_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "done_count.json"

    def always_held(src: str, dst: str) -> None:
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(atomic_json.os, "replace", always_held)
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        write_json_atomic(target, {"done_count": 3}, attempts=3)

    assert not target.exists()
    assert _leftover_tmp(tmp_path) == []


def test_write_json_atomic_replaces_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "token_ema.json"
    target.write_text('{"stale": true}', encoding="utf-8")

    write_json_atomic(target, {"verify": 10.0, "build": 5.0})

    # sort_keys keeps the on-disk shape stable across runs.
    assert target.read_text(encoding="utf-8") == json.dumps(
        {"build": 5.0, "verify": 10.0}, sort_keys=True, indent=2
    )
    assert _leftover_tmp(tmp_path) == []


@pytest.mark.parametrize(
    ("workflow_name", "expected"),
    [
        ("WORKFLOW.md", "token_ema.json"),
        ("WORKFLOW.demo.claude.md", "token_ema.WORKFLOW.demo.claude.json"),
        ("WORKFLOW.oneshot.md", "token_ema.WORKFLOW.oneshot.json"),
        ("my flow (copy).md", "token_ema.my-flow-copy.json"),
        ("WORKFLOW", "token_ema.WORKFLOW.json"),
    ],
)
def test_state_file_name_namespaces_non_default_workflows(
    workflow_name: str, expected: str
) -> None:
    assert state_file_name(Path("/repo") / workflow_name, "token_ema") == expected


def test_sibling_workflows_persist_to_distinct_state_files(tmp_path: Path) -> None:
    """Two orchestrators sharing `.symphony/` keep separate EMA and Done records."""
    cfg_a = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.demo.claude.md")
    cfg_b = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.demo.codex.md")
    cfg_default = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")

    orch = Orchestrator.__new__(Orchestrator)
    ema_a = Orchestrator._token_ema_path(orch, cfg_a)  # type: ignore[arg-type]
    ema_b = Orchestrator._token_ema_path(orch, cfg_b)  # type: ignore[arg-type]
    done_a = Orchestrator._done_count_path(orch, cfg_a)  # type: ignore[arg-type]
    done_b = Orchestrator._done_count_path(orch, cfg_b)  # type: ignore[arg-type]

    assert ema_a != ema_b and done_a != done_b
    assert ema_a.parent == ema_b.parent == tmp_path / ".symphony"
    # The canonical file name is untouched so existing deployments keep loading.
    assert Orchestrator._token_ema_path(orch, cfg_default) == (  # type: ignore[arg-type]
        tmp_path / ".symphony" / "token_ema.json"
    )
    assert Orchestrator._done_count_path(orch, cfg_default) == (  # type: ignore[arg-type]
        tmp_path / ".symphony" / "done_count.json"
    )

    orch._token_ema = {"build": 1.0}
    orch._done_count = 7
    Orchestrator._persist_token_ema(orch, cfg_a)  # type: ignore[arg-type]
    Orchestrator._persist_done_count(orch, cfg_a)  # type: ignore[arg-type]
    orch._token_ema = {"build": 2.0}
    orch._done_count = 9
    Orchestrator._persist_token_ema(orch, cfg_b)  # type: ignore[arg-type]
    Orchestrator._persist_done_count(orch, cfg_b)  # type: ignore[arg-type]

    assert json.loads(ema_a.read_text(encoding="utf-8")) == {"build": 1.0}
    assert json.loads(ema_b.read_text(encoding="utf-8")) == {"build": 2.0}
    assert json.loads(done_a.read_text(encoding="utf-8")) == {"done_count": 7}
    assert json.loads(done_b.read_text(encoding="utf-8")) == {"done_count": 9}
