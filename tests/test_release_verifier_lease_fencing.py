"""Fail-closed fencing for stale app-release verifier workers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from symphony.errors import SymphonyError
from symphony.orchestrator.run_registry import RunRegistry, registry_path_for_workflow
from symphony.trackers.file import FileBoardTracker

from tests.test_orchestrator_release_contract_integration import (
    _mutate_evidence,
    _orch,
    _seed_active_release_verifier,
    _setup_board,
    _setup_repo,
    _sync_native_statuses,
)


def _board_snapshot(board_root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(board_root.glob("*.md"))}


def test_stale_red_verifier_cannot_mutate_peer_owned_release_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "project switcher remains inert"
        runner = evidence["runner"]
        assert isinstance(runner, dict)
        runner["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    orchestrator = _orch(repo)
    original_gate, stale_run_id = _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=repo,
    )
    stale_entry = orchestrator._running[verifier.id]
    owner_registry = orchestrator._run_registry
    assert owner_registry is not None
    assert owner_registry.complete_run(
        issue_id=verifier.id,
        run_id=stale_run_id,
        status="lease-lost",
    )

    peer = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    try:
        peer_run_id = peer.acquire_run(
            verifier,
            workspace_path=tmp_path / "peer-workspace",
            attempt=1,
            attempt_kind="peer-release-verification",
            agent_kind="codex",
        )
        assert peer_run_id is not None
        current_gate = peer.get_release_gate(original_gate.finalizer_identifier)
        assert current_gate is not None
        assert peer.bind_release_verifier_run(
            gate=current_gate,
            verifier_run_id=peer_run_id,
        )
        peer_gate = peer.get_release_gate(original_gate.finalizer_identifier)
        assert peer_gate is not None
        assert peer_gate.verifier_run_id == peer_run_id

        tracker = FileBoardTracker(cfg.tracker)
        tracker.update_fields(verifier.identifier, state="Done")
        terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
        tracker.close()
        assert terminal_verifier is not None
        board_before = _board_snapshot(repo / "kanban")
        evidence_before = peer.get_release_evidence_identity(verifier.identifier)
        assert evidence_before is not None and not evidence_before.retired

        lifecycle_calls: list[str] = []
        rewind_calls: list[str] = []

        def forbidden_lifecycle(*_args: object, **_kwargs: object) -> object:
            lifecycle_calls.append("lifecycle")
            raise AssertionError("stale verifier reached RED lifecycle mutation")

        async def forbidden_rewind(**_kwargs: object) -> object:
            rewind_calls.append("rewind")
            raise AssertionError("stale verifier rewound the peer-owned board")

        monkeypatch.setattr(
            orchestrator,
            "_tracker_call_reconcile_release_cycle",
            forbidden_lifecycle,
        )
        monkeypatch.setattr(
            orchestrator,
            "_rewind_app_release_transition",
            forbidden_rewind,
        )

        with pytest.raises(
            SymphonyError,
            match="lost its active run lease before transition enforcement",
        ):
            asyncio.run(
                orchestrator._enforce_app_release_transition(
                    cfg=cfg,
                    issue=terminal_verifier,
                    workspace_path=repo,
                    producing_state="Verify",
                    known_app_release=True,
                    running_entry=stale_entry,
                )
            )

        assert lifecycle_calls == []
        assert rewind_calls == []
        assert _board_snapshot(repo / "kanban") == board_before
        after_gate = peer.get_release_gate(original_gate.finalizer_identifier)
        assert after_gate is not None
        assert after_gate == peer_gate
        evidence_after = peer.get_release_evidence_identity(verifier.identifier)
        assert evidence_after == evidence_before
        assert peer.release_verifier_run_is_authorized(
            gate=after_gate,
            verifier_issue_id=verifier.id,
        )
    finally:
        peer.close()


def test_serialized_red_loser_accepts_exact_retired_cycle_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    original_gate, _run_id = _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=repo,
    )
    stale_entry = orchestrator._running[verifier.id]
    registry = orchestrator._run_registry
    assert registry is not None
    replacement = registry.replace_pending_release_gate(
        replace(
            original_gate,
            verifier_issue_id="VERIFY-2",
            verifier_identifier="VERIFY-2",
            cycle_fingerprint="f" * 64,
            verifier_run_id=None,
        )
    )
    retired = registry.get_release_evidence_identity(verifier.identifier)
    assert retired is not None
    assert retired.retired
    assert retired.cycle_generation == stale_entry.release_gate_generation

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None
    board_before = _board_snapshot(repo / "kanban")
    forbidden_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        forbidden_calls.append("mutation")
        raise AssertionError("retired serialized loser reached a mutation path")

    async def forbidden_rewind(**_kwargs: object) -> object:
        forbidden_calls.append("rewind")
        raise AssertionError("retired serialized loser rewound the board")

    monkeypatch.setattr(
        "symphony.release.transition.validate_release_contract",
        forbidden,
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_reconcile_release_cycle",
        forbidden,
    )
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        forbidden_rewind,
    )

    transitioned, rewound = asyncio.run(
        orchestrator._enforce_app_release_transition(
            cfg=cfg,
            issue=terminal_verifier,
            workspace_path=repo,
            producing_state="Verify",
            known_app_release=True,
            running_entry=stale_entry,
        )
    )

    assert transitioned == terminal_verifier
    assert not rewound
    assert forbidden_calls == []
    assert _board_snapshot(repo / "kanban") == board_before
    assert registry.get_release_gate(original_gate.finalizer_identifier) == replacement
