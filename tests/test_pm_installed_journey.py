from __future__ import annotations

import json
import importlib.util
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import packages.pm.scripts.pm_workspace_installed_e2e as installed_runner
from packages.pm.scripts.pm_workspace_installed_e2e import (
    await_worker_release,
    exercise_fallback_authority,
    remove_planning_marker,
    set_fixture_paused,
)
from symphony.issue import Issue, PlanningRef
from symphony.orchestrator.run_registry import RunRegistry
from symphony.trackers.file import FileBoardTracker
from symphony.workflow import WorkflowState


ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "packages" / "pm" / "scripts" / "pm_workspace_installed_e2e.py"


def test_installed_fixture_hold_uses_durable_pause_flags(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    set_fixture_paused(project, ("PM-WORKER", "PM-EVIDENCE"), paused=True)
    registry = RunRegistry(project / ".symphony" / "state.db")
    try:
        for identifier in ("PM-WORKER", "PM-EVIDENCE"):
            flags = registry.get_issue_flags(identifier)
            assert flags is not None and flags.paused
            assert flags.pause_reason == "installed journey evidence hold"
    finally:
        registry.close()

    set_fixture_paused(project, ("PM-WORKER",), paused=False)
    registry = RunRegistry(project / ".symphony" / "state.db")
    try:
        worker = registry.get_issue_flags("PM-WORKER")
        evidence = registry.get_issue_flags("PM-EVIDENCE")
        assert worker is None or (not worker.paused and worker.pause_reason is None)
        assert evidence is not None and evidence.paused
    finally:
        registry.close()


def test_worker_release_waits_for_terminal_lease_and_preserves_identity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = project / ".symphony" / "state.db"
    registry = RunRegistry(database)
    issue = Issue(
        id="PM-WORKER",
        identifier="PM-WORKER",
        title="Worker",
        description="Fixture",
        priority=None,
        state="Done",
    )
    run_id = registry.acquire_run(
        issue,
        workspace_path=project,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id is not None
    registry.close()
    commit = "a" * 40

    def finish() -> None:
        time.sleep(0.05)
        finishing = RunRegistry(database)
        try:
            assert finishing.complete_run(
                issue_id=issue.id,
                run_id=run_id,
                status="completed",
                state="Done",
                commit_sha=commit,
            )
        finally:
            finishing.close()

    thread = threading.Thread(target=finish)
    thread.start()
    result = await_worker_release(
        project,
        "registered-project",
        issue.identifier,
        run_id,
        commit,
        timeout_seconds=2,
    )
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["run_id"] == run_id
    assert result["tested_commit"] == commit
    assert result["lease_active"] is False
    assert result["pm_dispatch_active"] is False
    assert result["owned_process_cleanup_confirmed"] is True


def test_worker_release_keeps_unknown_process_cleanup_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = project / ".symphony" / "state.db"
    registry = RunRegistry(database)
    issue = Issue(
        id="PM-UNCERTAIN",
        identifier="PM-UNCERTAIN",
        title="Uncertain",
        description="Fixture",
        priority=None,
        state="Done",
    )
    run_id = registry.acquire_run(
        issue,
        workspace_path=project,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id is not None
    commit = "b" * 40
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="completed",
        state="Done",
        commit_sha=commit,
    )
    registry._connect().execute(
        """UPDATE runs SET backend_agent_pid = 424242,
           backend_process_identity = NULL WHERE run_id = ?""",
        (run_id,),
    )
    registry.close()
    monkeypatch.setattr(installed_runner, "process_identity", lambda _pid: None)
    monkeypatch.setattr(
        installed_runner, "process_group_exists", lambda _pid: None
    )
    with pytest.raises(AssertionError, match="cleanup did not finish"):
        await_worker_release(
            project,
            "registered-project",
            issue.identifier,
            run_id,
            commit,
            timeout_seconds=0.2,
        )

def test_installed_fixture_fallback_probe_restores_registry(tmp_path: Path) -> None:
    module = _runner_module()
    root = tmp_path / "fallback-setup"
    project, _planning, _server = module.setup(
        root, Path("/usr/bin/true"), _port()
    )
    project_id = "registered-pm-installed-e2e"
    identifier = "PM-FALLBACK"
    registry = RunRegistry(project / ".symphony" / "state.db")
    assert registry.create_pm_handoff_batch(
        handoff_id="handoff-fallback",
        operation_id="operation-fallback",
        project_id=project_id,
        change_id="change-fallback",
        scope_hash="sha256:scope",
        approval_id="approval-fallback",
        snapshot_id="approval-fallback",
        items=({
            "task_id": "T-FALLBACK",
            "task_revision": "revision-fallback",
            "deterministic_ticket_id": identifier,
            "content_hash": "sha256:content",
            "input_hash": "sha256:input",
            "input_json": "{}",
            "required_procedures_json": "{}",
        },),
    )
    registry.close()
    state = WorkflowState(project / "WORKFLOW.md")
    config, error = state.reload()
    assert config is not None and error is None, error
    tracker = FileBoardTracker(config.tracker)
    tracker.create_validated(
        identifier=identifier,
        title="Fallback",
        state="Todo",
        description="Fallback proof",
        planning_ref=PlanningRef(
            "change-fallback", "T-FALLBACK", "revision-fallback",
            "handoff-fallback", "approval-fallback",
        ),
    )
    remove_planning_marker(project / "kanban" / f"{identifier}.md")
    database = project / ".symphony" / "state.db"
    before = database.read_bytes()
    result = exercise_fallback_authority(project, project_id, identifier)
    assert result["recognized"] is True
    assert result["runnable"] is False
    assert "fallback" in str(result["reason"])
    assert database.read_bytes() == before
    restored = RunRegistry(database)
    try:
        assert restored.get_pm_managed_ticket(project_id, identifier) is not None
    finally:
        restored.close()

def _runner_module():
    spec = importlib.util.spec_from_file_location("pm_workspace_installed_e2e", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_runner_prepares_distinct_same_slug_projects_and_evidence_policies(
    tmp_path: Path,
) -> None:
    module = _runner_module()
    root = tmp_path / "runner-setup"
    project, planning, server = module.setup(root, Path("/usr/bin/true"), _port())
    registry = json.loads((root / "projects.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in registry["projects"]] == [
        "registered-pm-installed-e2e", "registered-pm-installed-e2e-peer",
    ]
    workflows = [
        Path(item["workflow"]).read_text(encoding="utf-8")
        for item in registry["projects"]
    ]
    assert all("project_slug: pm-installed-e2e" in value for value in workflows)
    assert "manual_verification_procedures:" in workflows[0]
    assert "model: gpt-6.1-sol" in workflows[0]
    baseline = planning / "openspec" / "specs" / "requirements" / "spec.md"
    assert "symphony:req=REQ-A1B2C3D4" in baseline.read_text(encoding="utf-8")
    assert (project / "reports" / "manual-ux.json").is_file()
    assert not (project / ".enable-dispatch").exists()
    assert server.is_file()
    compile(server.read_text(encoding="utf-8"), str(server), "exec")


@pytest.mark.skipif(
    not os.environ.get("SYMPHONY_PM_INSTALLED_PYTHON"),
    reason="set SYMPHONY_PM_INSTALLED_PYTHON to run the clean-wheel journey",
)
def test_clean_wheel_review_handoff_accept_archive_journey(tmp_path: Path) -> None:
    # Keep the venv launcher path intact; resolving it follows the interpreter
    # symlink back to the base Python and bypasses the clean wheel install.
    python = Path(os.environ["SYMPHONY_PM_INSTALLED_PYTHON"]).absolute()
    openspec = Path(os.environ["SYMPHONY_PM_OPENSPEC"]).resolve()
    wheel = Path(os.environ["SYMPHONY_PM_WHEEL"]).resolve()
    source_sha = os.environ["SYMPHONY_PM_SOURCE_SHA"]
    root = tmp_path / "installed-journey"
    subprocess.run(
        [
            str(python), "-I", str(RUNNER),
            "--root", str(root),
            "--openspec", str(openspec),
            "--port", str(_port()),
            "--assistance",
            "--browser",
            "--wheel", str(wheel),
            "--source-sha", source_sha,
            "--expected-prefix", str(python.parents[1]),
        ],
        check=True,
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        timeout=900,
    )
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["validation"]["valid"] is True
    assert receipt["assistance"]["applied"] is True
    assert receipt["assistance"]["invalid_digest_rejected"] is True
    assert receipt["assistance"]["cancelled"] is True
    assert "created_change_from_idea_in_ui" in receipt["browser"]["actions"]
    assert "created_renamed_reordered_deleted_and_restored_server_work_ids" in receipt["browser"]["actions"]
    assert "toggled_wbs_tree_by_keyboard" in receipt["browser"]["actions"]
    assert len(receipt["browser"]["wbs_tree_keyboard"]["presses"]) == 12
    assert "opened_real_worker_artifact_in_ui" in receipt["browser"]["actions"]
    assert "confirmed_native_handoff_in_ui" in receipt["browser"]["actions"]
    assert "accepted_verified_revision_in_ui" in receipt["browser"]["actions"]
    assert "confirmed_archive_in_ui" in receipt["browser"]["actions"]
    assert "reopened_original_change_id_as_readonly_history" in receipt["browser"]["actions"]
    assert receipt["browser"]["requirements_delta"]["accepted_baseline_exists"] is True
    assert receipt["browser"]["requirements_delta"]["same_requirement_modified"] is True
    assert receipt["execution"]["scheduler_dispatched"] is True
    assert receipt["execution"]["agent_kind"] == "codex"
    assert receipt["execution"]["model"] == "gpt-6.1-sol"
    reach = receipt["combined_recovery_reach"]
    assert reach["staging_response_lost"] is True
    assert reach["restart_reconciled"] is True
    assert reach["staged_item_count"] == 4
    assert reach["partial_ticket_count"] == 0
    assert reach["partial_run_count"] == 0
    assert reach["server_restarted"] is True
    assert reach["marker_removed"] is True
    assert reach["fallback_recognition"]["recognized"] is True
    assert reach["fallback_recognition"]["runnable"] is False
    assert "fallback" in reach["fallback_recognition"]["reason"]
    assert reach["durable_mapping_count"] == 1
    assert reach["automatic_delivery_conflict"] is True
    assert reach["durable_failure_class"] == "pm_delivery_policy_conflict"
    assert reach["revocation_record_count"] == 1
    assert reach["scheduler_denial_record_count"] == 1
    assert reach["native_process_count"] == 0
    assert reach["surviving_dispatch_permit"] is False
    assert reach["dispatch_entry_scope"] == {
        "initial_attempt_denied_before_backend": True,
        "later_turn_spawn_reached": False,
        "phase_rebuild_reached": False,
        "backend_quota_fallback_reached": False,
        "reason": (
            "automatic-delivery policy is checked before backend construction, "
            "so later backend entry points are preempted"
        ),
    }
    assert reach["ticket_identifier"] == receipt["evidence_modes"]["native_no_run"]["ticket_identifier"]
    assert receipt["post_acceptance_native_denial"] == {
        "board_state": "Done",
        "mapping_state": "REVOKED",
        "revocation_record_count": 1,
        "active_permit": False,
        "authority_runnable": False,
        "reserve_denied": True,
    }
    assert receipt["delivery"]["remote_count"] == 0
    assert receipt["delivery"]["source_commit"] == receipt["tested_commit"]
    assert len(receipt["delivery"]["tree_sha"]) == 40
    assert receipt["installation"]["source_sha"] == source_sha
    assert receipt["installation"]["wheel_sha256"].startswith("sha256:")
    assert receipt["installation"]["distributions"]["oh-my-symphony"]
    assert receipt["boundaries"]["raw_work_item_identity_attack_rejected"] is True
    assert receipt["boundaries"]["client_change_identity_rejected"] is True
    assert receipt["boundaries"]["same_slug_registered_project_refused"] is True
    assert receipt["boundaries"]["valid_cross_project_acceptance_token_rejected"] is True
    assert receipt["boundaries"]["registered_project_id"] != receipt["boundaries"]["peer_registered_project_id"]
    assert receipt["boundaries"]["registered_project_id"] != "pm-installed-e2e"
    assert receipt["boundaries"]["peer_registered_project_id"] != "pm-installed-e2e"
    assert receipt["boundaries"]["accepted_baseline_requirement_id"].startswith("REQ-")
    assert receipt["boundaries"]["accepted_baseline_scenario_id"].startswith("SC-")
    assert set(receipt["evidence_modes"]) == {
        "native_no_run", "manual_only", "mixed",
    }
    assert all(
        value["native_process_count"] == 0
        and value["acceptance_id"]
        and value["state_before_evidence"] == "Todo"
        and value["state_after_evidence"] == "Todo"
        and value["state_after_acceptance"] == "Done"
        and value["timestamp_source"] == "fixture-reported host observations"
        for value in receipt["evidence_modes"].values()
    )
    assert receipt["evidence_modes"]["native_no_run"]["scheduler_run_count"] == 1
    assert receipt["evidence_modes"]["manual_only"]["scheduler_run_count"] == 0
    assert receipt["evidence_modes"]["mixed"]["scheduler_run_count"] == 0
    assert receipt["evidence_modes"]["native_no_run"]["receipt_count"] == 1
    assert receipt["evidence_modes"]["manual_only"]["receipt_count"] == 1
    assert receipt["evidence_modes"]["mixed"]["receipt_count"] == 2
    assert receipt["evidence_modes"]["native_no_run"]["procedure_kinds"] == ["native"]
    assert receipt["evidence_modes"]["native_no_run"]["return_codes"] == [0]
    assert receipt["evidence_modes"]["manual_only"]["procedure_kinds"] == ["manual"]
    assert receipt["evidence_modes"]["manual_only"]["return_codes"] == [None]
    assert set(receipt["evidence_modes"]["mixed"]["procedure_kinds"]) == {
        "native", "manual",
    }
    assert receipt["archive_history"]["state"] == "Archived"
    assert all(receipt["archive_history"]["denied_mutations"].values())
    assert receipt["archive_history"]["artifact_refs"]
    assert Path(receipt["archive"]["archived_path"]).is_dir()
