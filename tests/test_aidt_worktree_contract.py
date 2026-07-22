"""Closed public contracts for workflow-scoped AIDT worktrees."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from symphony.aidt_worktree.contract import (
    AIDT_COMPLETION_AUTHORIZATION_SCHEMA,
    AIDT_WORKTREE_BASE_REF,
    AidtWorktreeFailure,
    AidtWorktreeResult,
    CompletionAuthorization,
    DelegateDisposition,
    DelegateResult,
    change_kind_for_issue_type,
    derive_aidt_branch,
    load_aidt_worktree_settings,
    stable_worktree_paths,
    validate_aidt_branch,
)

from tests.aidt_routing_support import routing_config, service_config, service_definition


def _config(tmp_path: Path, *, enabled: bool = True) -> Any:
    board = tmp_path / "board"
    board.mkdir(exist_ok=True)
    raw = routing_config(tmp_path, [service_definition()])
    raw["aidt_worktree"] = {"enabled": enabled}
    config = service_config(board, raw)
    return replace(
        config,
        workflow_path=(tmp_path / "WORKFLOW.md").resolve(),
        workspace_root=(tmp_path / "workspaces").resolve(),
        workspace_reuse_policy="preserve",
        hooks=SimpleNamespace(
            after_create=None,
            before_run=None,
            after_run=None,
            before_remove=None,
            after_done=None,
        ),
        agent=SimpleNamespace(
            kind="codex",
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
    )


def test_worktree_profile_is_default_off_and_side_effect_free(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.raw.pop("aidt_worktree")

    assert load_aidt_worktree_settings(config) is None
    assert not (tmp_path / ".symphony").exists()
    assert not (tmp_path / ".symphony-aidt-worktrees").exists()


def test_enabled_profile_derives_stable_workflow_relative_identity(tmp_path: Path) -> None:
    settings = load_aidt_worktree_settings(_config(tmp_path))

    assert settings is not None
    assert settings.enabled is True
    assert settings.workflow_identity == settings.paths.workflow_identity
    assert settings.paths.root == tmp_path / ".symphony" / "aidt-worktrees-v1"
    assert settings.paths.activation == settings.paths.root / "ACTIVATED.json"
    assert len(settings.workflow_generation) == 64
    assert settings.workspace_root == (tmp_path / "workspaces").resolve()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: cfg.raw.__setitem__("aidt_worktree", {"enabled": True, "extra": 1}),
        lambda cfg: cfg.raw.__setitem__("aidt_worktree", {"enabled": 1}),
        lambda cfg: cfg.raw.__setitem__("aidt_routing", {"enabled": False}),
        lambda cfg: object.__setattr__(cfg, "workspace_reuse_policy", "refresh"),
        lambda cfg: object.__setattr__(cfg, "workspace_root", Path("relative")),
        lambda cfg: object.__setattr__(cfg.tracker, "kind", "linear"),
        lambda cfg: setattr(cfg.hooks, "before_run", "git fetch"),
        lambda cfg: setattr(cfg.agent, "auto_commit_on_done", True),
        lambda cfg: setattr(cfg.agent, "auto_merge_on_done", True),
    ],
)
def test_enabled_profile_rejects_every_generic_mutation_seam(
    tmp_path: Path, mutate: Any
) -> None:
    config = _config(tmp_path)
    mutate(config)

    with pytest.raises(AidtWorktreeFailure, match="profile_invalid"):
        load_aidt_worktree_settings(config)


def test_disabled_profile_remains_inert_but_is_still_closed(tmp_path: Path) -> None:
    assert load_aidt_worktree_settings(_config(tmp_path, enabled=False)) is None
    config = _config(tmp_path, enabled=False)
    config.raw["aidt_worktree"]["unknown"] = True

    with pytest.raises(AidtWorktreeFailure, match="profile_invalid"):
        load_aidt_worktree_settings(config)
    assert not (tmp_path / ".symphony").exists()


@pytest.mark.parametrize(
    ("issue_type", "kind", "expected"),
    [
        (" Bug ", "backend", "fix/A20-1188"),
        ("story", "backend", "feat/A20-1188"),
        ("task", "frontend", "csk-feat/A20-1188"),
        ("sub-task", "frontend", "csk-feat/A20-1188"),
        ("improvement", "backend", "feat/A20-1188"),
        ("new feature", "frontend", "csk-feat/A20-1188"),
    ],
)
def test_change_kind_and_branch_are_derived_from_closed_inputs(
    issue_type: str, kind: str, expected: str
) -> None:
    change_kind = change_kind_for_issue_type(issue_type)
    branch = derive_aidt_branch("A20-1188", kind, change_kind)

    assert branch == expected
    assert validate_aidt_branch(branch, "A20-1188", kind, change_kind) == branch
    assert AIDT_WORKTREE_BASE_REF == "refs/remotes/origin/aidt-prd"


@pytest.mark.parametrize(
    "issue_type", ["epic", "new  feature", "story\n", "STORY\u0000"]
)
def test_change_kind_rejects_unreviewed_or_control_issue_types(issue_type: str) -> None:
    with pytest.raises(AidtWorktreeFailure, match="change_kind_invalid"):
        change_kind_for_issue_type(issue_type)


@pytest.mark.parametrize(
    "branch",
    [
        "aidt-prd",
        "aidt-dev",
        "release/A20-1188",
        "feat/A20-1188-extra",
        "Feat/A20-1188",
        "../feat/A20-1188",
    ],
)
def test_branch_validation_rejects_protected_alias_and_suffix_forms(branch: str) -> None:
    with pytest.raises(AidtWorktreeFailure, match="branch_invalid"):
        validate_aidt_branch(branch, "A20-1188", "backend", "feat")


def test_child_paths_are_stable_contained_and_case_canonical(tmp_path: Path) -> None:
    paths = stable_worktree_paths(
        (tmp_path / "WORKFLOW.md").resolve(), "A20-1188--viewer-api"
    )

    assert paths.manifest == paths.root / "manifests/A20-1188--viewer-api.json"
    assert paths.ownership == paths.root / "ownership/A20-1188--viewer-api.json"
    assert paths.attempt == paths.root / "attempts/A20-1188--viewer-api.json"
    assert paths.manifest_lock.name.endswith(".lock")
    with pytest.raises(AidtWorktreeFailure, match="identifier_invalid"):
        stable_worktree_paths((tmp_path / "WORKFLOW.md").resolve(), "A20-1--Viewer")


def _authorization(**overrides: Any) -> CompletionAuthorization:
    fields: dict[str, Any] = {
        "schema": AIDT_COMPLETION_AUTHORIZATION_SCHEMA,
        "identifier": "A20-1188--viewer-api",
        "workflow_generation": "a" * 64,
        "route_pair_digest": "b" * 64,
        "ready_manifest_revision": 2,
        "issue_id": "A20-1188--viewer-api",
        "run_id": "c" * 32,
        "attempt_kind": "initial",
        "owning_lease_token": "c" * 32,
        "final_transition_identity": "d" * 64,
        "issuer": "aidt-stage-controller-v1",
        "issued_at": "2026-07-21T01:02:03Z",
        "authorization_digest": "e" * 64,
    }
    fields.update(overrides)
    return CompletionAuthorization(**fields)


def test_completion_authorization_is_exact_frozen_and_not_self_authorizing() -> None:
    token = _authorization()

    assert token.run_id == token.owning_lease_token
    with pytest.raises(AidtWorktreeFailure, match="authorization_invalid"):
        _authorization(owning_lease_token="f" * 32)
    with pytest.raises(AidtWorktreeFailure, match="authorization_invalid"):
        _authorization(ready_manifest_revision=True)
    with pytest.raises(AidtWorktreeFailure, match="authorization_invalid"):
        _authorization(issuer="generic-done")
    with pytest.raises(AidtWorktreeFailure, match="authorization_invalid"):
        _authorization(workflow_generation=1)


def test_public_dtos_fail_closed_for_wrong_runtime_scalar_types(tmp_path: Path) -> None:
    with pytest.raises(AidtWorktreeFailure, match="branch_invalid"):
        derive_aidt_branch("A20-1188", [], "feat")
    with pytest.raises(AidtWorktreeFailure, match="internal_error"):
        AidtWorktreeResult(Path("relative"), False, 1)


def test_delegate_result_has_four_closed_fallback_dispositions(tmp_path: Path) -> None:
    unmanaged = DelegateResult.unmanaged()
    handled = DelegateResult.handled(AidtWorktreeResult(tmp_path.resolve(), False, 2))
    preserved = DelegateResult.owned_preserved("authorization_invalid")
    failed = DelegateResult.owned_error("manifest_invalid")

    assert unmanaged.disposition is DelegateDisposition.UNMANAGED
    assert handled.disposition is DelegateDisposition.HANDLED
    assert handled.value is not None
    assert preserved.disposition is DelegateDisposition.OWNED_PRESERVED
    assert failed.disposition is DelegateDisposition.OWNED_ERROR
    assert "private" not in repr(AidtWorktreeFailure("invalid", "/private/secret"))
