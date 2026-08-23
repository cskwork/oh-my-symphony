"""Coverage contracts for orchestrator policy, authority, and scheduling."""
# ruff: noqa: F405

from tests.coverage_cases._orchestrator_support import *  # noqa: F403


def test_core_board_error_and_retry_predicates_fail_closed() -> None:
    assert core_module._is_closed_client_runtime_error(
        RuntimeError("client has already been closed")
    )
    assert not core_module._is_closed_client_runtime_error(ValueError("closed"))
    assert (
        core_module._clean_board_error_message("\x1b[31mboom\x1b[0m\x00\n now")
        == "boom now"
    )
    assert not core_module._is_retryable_auto_pause_reason(None)
    assert not core_module._is_retryable_auto_pause_reason("ordinary pause")
    assert core_module._is_retryable_auto_pause_reason(
        "worker error: rate limit; paused for operator inspection"
    )
    assert core_module._is_retryable_worker_error("opencode", "worker_exit", "exit -15")
    assert not core_module._is_retryable_worker_error(
        "codex", "worker_exit", "exit -15"
    )


def test_worktree_status_snapshot_returns_none_when_git_cannot_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        core_module.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 1)),
    )
    assert core_module._worktree_status_snapshot(tmp_path) is None


def test_core_registry_open_and_release_authority_failures_are_observable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._run_registry_initialized = False
    subject._run_registry = SimpleNamespace(
        path=tmp_path / "old.db", close=lambda: None
    )
    subject._registry_error_count = 0
    subject._last_registry_error = None
    subject._stats = "stats"
    cfg = _cfg()
    cfg.workflow_path = tmp_path / "WORKFLOW.md"
    monkeypatch.setattr(
        core_module,
        "RunRegistry",
        lambda _path: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )
    registry, fresh = orchestrator._open_run_registry(cfg)
    assert registry is None and fresh is False
    assert orchestrator._registry_error_count == 1
    assert orchestrator.stats == "stats"

    monkeypatch.setattr(
        orchestrator, "_ensure_run_registry_open_only", lambda _cfg: None
    )
    with pytest.raises(SymphonyError, match="authority registry is unavailable"):
        orchestrator._release_registry_required(cfg)


def test_registry_open_maintenance_reports_expiration_and_skips_reused_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    calls = iter([2, []])
    monkeypatch.setattr(orchestrator, "_registry_guard", lambda *_a: next(calls))
    rehydrated: list[list[Any]] = []
    monkeypatch.setattr(
        orchestrator,
        "_rehydrate_issue_flags",
        lambda flags, **_kwargs: rehydrated.append(flags),
    )
    registry = SimpleNamespace(
        path=Path("state.db"), expire_stale=lambda: 2, list_issue_flags=lambda: []
    )
    orchestrator._finish_run_registry_open(cast(Any, registry), _cfg(), registry.path)
    assert rehydrated == [[]]

    monkeypatch.setattr(orchestrator, "_open_run_registry", lambda _cfg: (None, False))
    orchestrator._ensure_run_registry(_cfg())


def test_pending_release_gate_requires_exact_readback_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    gate = _gate()
    written = replace(gate, generation="generation-1")
    calls = iter([written, None])
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: next(calls))
    with pytest.raises(SymphonyError, match="not durably persisted"):
        orchestrator._persist_pending_release_gate(
            cfg=_cfg(), gate=gate, operation="replace"
        )

    calls = iter([gate, gate])
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: next(calls))
    with pytest.raises(SymphonyError, match="lacks a durable cycle generation"):
        orchestrator._persist_pending_release_gate(
            cfg=_cfg(), gate=gate, operation="replace"
        )


def test_initial_release_gate_rejects_invalid_board_and_contract_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    verifier = _issue("VERIFY-1", state="Verify")
    monkeypatch.setattr(
        core_module, "_has_active_release_verify_lane", lambda _cfg: False
    )
    monkeypatch.setattr(core_module, "_has_release_finalizer_lane", lambda _cfg: True)
    with pytest.raises(SymphonyError, match="requires active Verify"):
        orchestrator._create_initial_release_gate(cfg, verifier)

    monkeypatch.setattr(
        core_module, "_has_active_release_verify_lane", lambda _cfg: True
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_kwargs: SimpleNamespace(
            errors=("bad contract",),
            finalizer_ticket="APP-FINAL",
            contract_sha256="a" * 64,
        ),
    )
    with pytest.raises(SymphonyError, match="cannot bind initial"):
        orchestrator._create_initial_release_gate(cfg, verifier)

    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_kwargs: SimpleNamespace(
            errors=(), finalizer_ticket="APP-FINAL", contract_sha256="a" * 64
        ),
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    with pytest.raises(SymphonyError, match="finalizer is missing"):
        orchestrator._create_initial_release_gate(cfg, verifier)

    finalizer = _issue("APP-FINAL", labels=("app-release-finalizer",))
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: finalizer,
    )
    with pytest.raises(SymphonyError, match="must be blocked by its verifier"):
        orchestrator._create_initial_release_gate(cfg, verifier)


def test_finalizer_guard_rejects_missing_terminal_and_wrong_run_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    gate = _approved_core_gate()
    finalizer, verifier = _finalizer_and_verifier()

    _configure_finalizer_guard(
        monkeypatch, orchestrator, finalizer=None, verifier=verifier
    )
    with pytest.raises(SymphonyError, match="could not be read"):
        orchestrator._guard_release_finalizer(cfg=cfg, issue=finalizer, gate=gate)

    _configure_finalizer_guard(
        monkeypatch,
        orchestrator,
        finalizer=replace(finalizer, state="Blocked"),
        verifier=verifier,
    )
    with pytest.raises(SymphonyError, match="non-success terminal"):
        orchestrator._guard_release_finalizer(cfg=cfg, issue=finalizer, gate=gate)

    _configure_finalizer_guard(
        monkeypatch, orchestrator, finalizer=finalizer, verifier=verifier
    )
    with pytest.raises(SymphonyError, match="not bound to this gate cycle"):
        orchestrator._guard_release_finalizer(
            cfg=cfg,
            issue=finalizer,
            gate=gate,
            expected_run_id="different-run",
            require_run_authority=True,
        )


def test_finalizer_guard_invalidates_changed_target_and_completion_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    finalizer, verifier = _finalizer_and_verifier()
    gate = _approved_core_gate()
    _configure_finalizer_guard(
        monkeypatch, orchestrator, finalizer=finalizer, verifier=verifier
    )
    reopened: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_reopen_stale_release_gate",
        lambda **kwargs: reopened.append(kwargs["reason"]),
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_kwargs: SimpleNamespace(
            errors=(),
            target_branch="dev",
            target_sha="b" * 40,
            contract_sha256="c" * 64,
            finalizer_ticket="OTHER-FINAL",
        ),
    )
    with pytest.raises(SymphonyError, match="approval became stale"):
        orchestrator._guard_release_finalizer(cfg=cfg, issue=finalizer, gate=gate)
    assert "approved target branch changed" in reopened[0]
    assert "release contract finalizer changed" in reopened[0]

    completed_gate = replace(gate, finalizer_completed_at=NOW)
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_kwargs: SimpleNamespace(
            errors=(),
            target_branch="main",
            target_sha="b" * 40,
            contract_sha256="a" * 64,
            finalizer_ticket="APP-FINAL",
        ),
    )
    monkeypatch.setattr(
        core_module, "_release_ticket_version_token", lambda *_a: "token"
    )
    with pytest.raises(SymphonyError, match="approval became stale"):
        orchestrator._guard_release_finalizer(
            cfg=cfg, issue=finalizer, gate=completed_gate
        )
    assert "lacks a host-observed" in reopened[-1]


def test_finalizer_completion_proof_requires_durable_readback_and_stable_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    finalizer, _verifier = _finalizer_and_verifier()
    finalizer = replace(finalizer, state="Done")
    gate = _approved_core_gate()
    monkeypatch.setattr(
        core_module, "_release_ticket_version_token", lambda *_a: "token"
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: finalizer,
    )
    calls = iter([False, None])
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: next(calls))
    with pytest.raises(SymphonyError, match="could not be persisted"):
        orchestrator._mark_release_finalizer_completed(
            cfg=cfg,
            issue=finalizer,
            gate=gate,
            completion_token="token",
            rewind_state="Document",
        )

    persisted = replace(
        gate,
        finalizer_completed_at=NOW,
        finalizer_completion_token="token",
    )
    calls = iter([True, persisted])
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: next(calls))
    fetches = iter([finalizer, None])
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: next(fetches),
    )
    invalidated: list[bool] = []
    monkeypatch.setattr(
        orchestrator,
        "_invalidate_release_finalizer_version",
        lambda **_kwargs: invalidated.append(True),
    )
    with pytest.raises(SymphonyError, match="changed during completion proof"):
        orchestrator._mark_release_finalizer_completed(
            cfg=cfg,
            issue=finalizer,
            gate=gate,
            completion_token="token",
            rewind_state="Document",
        )
    assert invalidated == [True]

    calls = iter([True, persisted])
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: next(calls))
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: finalizer,
    )
    assert (
        orchestrator._mark_release_finalizer_completed(
            cfg=cfg,
            issue=finalizer,
            gate=gate,
            completion_token="token",
            rewind_state="Document",
        )
        == persisted
    )


def test_finalizer_version_guard_invalidates_midcheck_ticket_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    finalizer, _verifier = _finalizer_and_verifier()
    gate = _approved_core_gate()
    tokens = iter(["before", "after"])
    monkeypatch.setattr(
        core_module, "_release_ticket_version_token", lambda *_a: next(tokens)
    )
    monkeypatch.setattr(
        orchestrator, "_guard_release_finalizer", lambda **_kwargs: finalizer
    )
    invalidated: list[bool] = []
    monkeypatch.setattr(
        orchestrator,
        "_invalidate_release_finalizer_version",
        lambda **_kwargs: invalidated.append(True),
    )
    with pytest.raises(SymphonyError, match="changed during terminal guard"):
        orchestrator._guard_release_finalizer_with_version(
            cfg=_cfg(),
            issue=finalizer,
            gate=gate,
            rewind_state="Document",
            expected_run_id="finalizer-run",
            require_run_authority=True,
        )
    assert invalidated == [True]


def test_running_release_authority_reports_each_lost_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    verifier = _issue("VERIFY-1", state="Verify")
    ordinary = _release_entry(
        verifier,
        known_app_release=False,
        known_release_cycle_verifier=False,
        known_app_release_finalizer=False,
    )
    assert (
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, ordinary)
        )
        == verifier
    )

    missing_cache = _release_entry(verifier, run_id="")
    with pytest.raises(SymphonyError, match="lacks cached host authority"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, missing_cache)
        )

    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: None)
    with pytest.raises(SymphonyError, match="gate disappeared"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, _release_entry(verifier))
        )

    mismatched = _approved_core_gate(generation="other")
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: mismatched)
    with pytest.raises(SymphonyError, match="generation changed"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, _release_entry(verifier))
        )

    finalizer, _done_verifier = _finalizer_and_verifier()
    finalizer_entry = _release_entry(
        finalizer,
        known_release_cycle_verifier=False,
        known_app_release_finalizer=True,
        run_id="different-run",
    )
    gate = _approved_core_gate()
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    with pytest.raises(SymphonyError, match="lost its exact run binding"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, finalizer_entry)
        )

    finalizer_entry.run_id = "finalizer-run"
    monkeypatch.setattr(
        orchestrator, "_guard_release_finalizer", lambda **_kwargs: finalizer
    )
    no_sha = replace(gate, approved_target_sha=None)
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: no_sha)
    with pytest.raises(SymphonyError, match="no approved target SHA"):
        orchestrator._require_running_release_authority(
            cfg=cfg,
            entry=cast(Any, finalizer_entry),
            workspace_path=tmp_path,
        )

    wrong_verifier = _release_entry(verifier, run_id="wrong-run")
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    with pytest.raises(SymphonyError, match="verifier lost its exact run binding"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, wrong_verifier)
        )

    verifier_entry = _release_entry(verifier)
    responses = iter([gate, False])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    with pytest.raises(SymphonyError, match="no longer authorized"):
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, verifier_entry)
        )

    finalizer_entry.run_id = "finalizer-run"
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        orchestrator, "_guard_release_finalizer", lambda **_kwargs: finalizer
    )
    assert (
        orchestrator._require_running_release_authority(
            cfg=cfg, entry=cast(Any, finalizer_entry)
        )
        == finalizer
    )


def test_release_transition_wraps_authority_loss_as_stale_worker_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    entry = _release_entry(_issue("VERIFY-1", state="Verify"))
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a: True)
    monkeypatch.setattr(
        orchestrator,
        "_require_running_release_authority",
        lambda **_kwargs: (_ for _ in ()).throw(SymphonyError("stale gate")),
    )
    with pytest.raises(
        core_module._ReleaseTransitionAuthorityLost, match="no longer owns"
    ):
        orchestrator._require_release_transition_verifier_authority(
            cfg=_cfg(), issue=entry.issue, entry=cast(Any, entry)
        )


def test_release_dispatch_preparation_fails_closed_for_orphaned_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    ordinary = _issue("APP-1")
    monkeypatch.setattr(
        orchestrator,
        "_release_registry_call",
        lambda *_a: (_ for _ in ()).throw(SymphonyError("registry unavailable")),
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_kwargs: SimpleNamespace(errors=("no contract",)),
    )
    assert orchestrator._prepare_release_dispatch(ordinary, cfg).issue == ordinary

    identity = ReleaseEvidenceIdentity(
        issue_id=ordinary.id,
        identifier=ordinary.identifier,
        finalizer_identifier="APP-FINAL",
        role="verifier",
        cycle_generation="generation",
        retired=True,
        recorded_at=NOW,
        updated_at=NOW,
    )
    responses = iter([None, None, identity])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    with pytest.raises(SymphonyError, match="evidence-only"):
        orchestrator._prepare_release_dispatch(ordinary, cfg)

    responses = iter([None, None, replace(identity, retired=False)])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    with pytest.raises(SymphonyError, match="no host-owned gate"):
        orchestrator._prepare_release_dispatch(ordinary, cfg)

    finalizer = _issue("APP-FINAL", labels=("app-release-finalizer",))
    responses = iter([None, None, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    with pytest.raises(SymphonyError, match="finalizer has no host-owned authority"):
        orchestrator._prepare_release_dispatch(finalizer, cfg)


def test_release_dispatch_preparation_requires_bound_finalizer_through_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    verifier = _issue("VERIFY-1", state="Verify")
    pending = replace(_gate(), generation="generation")
    responses = iter([pending, None, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    with pytest.raises(SymphonyError, match="disappeared while verifier was pending"):
        orchestrator._prepare_release_dispatch(verifier, cfg)

    approved = _approved_core_gate()
    responses = iter([approved, None, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    with pytest.raises(SymphonyError, match="disappeared while reopening"):
        orchestrator._prepare_release_dispatch(verifier, cfg)

    finalizer = _issue("APP-FINAL", state="Document")
    responses = iter([approved, None, None, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: finalizer,
    )
    monkeypatch.setattr(orchestrator, "_reopen_stale_release_gate", lambda **_k: None)
    with pytest.raises(SymphonyError, match="authority disappeared"):
        orchestrator._prepare_release_dispatch(verifier, cfg)

    monkeypatch.setattr(
        core_module.ReleaseCycleService,
        "restore_verifier_gate_labels",
        lambda _self, *, issue, **_kwargs: issue,
    )
    responses = iter([approved, None, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    terminal_verifier = replace(verifier, state="Done")
    authority = orchestrator._prepare_release_dispatch(terminal_verifier, cfg)
    assert authority.issue == terminal_verifier
    assert authority.gate == approved


def test_release_verifier_handoff_requires_new_identity_and_finalizer_relink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    identity = ReleaseEvidenceIdentity(
        issue_id="old-id",
        identifier="VERIFY-OLD",
        finalizer_identifier="APP-FINAL",
        role="verifier",
        cycle_generation="old-generation",
        retired=True,
        recorded_at=NOW,
        updated_at=NOW,
    )
    gate = _approved_core_gate(
        verifier_issue_id="new-id",
        verifier_identifier="VERIFY-NEW",
        generation="new-generation",
    )

    registry = SimpleNamespace(
        has_active_lease=lambda _issue_id: False,
        get_release_gate=lambda _identifier: None,
        get_release_evidence_identity_by_issue_id=lambda _issue_id: None,
    )
    assert not orchestrator._release_verifier_handoff_is_durable(
        cfg=_cfg(), registry=cast(Any, registry), identity=identity
    )

    registry.get_release_gate = lambda _identifier: gate
    assert not orchestrator._release_verifier_handoff_is_durable(
        cfg=_cfg(), registry=cast(Any, registry), identity=identity
    )

    current = replace(
        identity,
        issue_id="new-id",
        identifier="VERIFY-NEW",
        cycle_generation="new-generation",
        retired=False,
    )
    registry.get_release_evidence_identity_by_issue_id = lambda _issue_id: current
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    assert not orchestrator._release_verifier_handoff_is_durable(
        cfg=_cfg(), registry=cast(Any, registry), identity=identity
    )

    finalizer = _issue(
        "APP-FINAL",
        blocked_by=(BlockerRef("new-id", "VERIFY-NEW", "Verify"),),
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: finalizer,
    )
    assert orchestrator._release_verifier_handoff_is_durable(
        cfg=_cfg(), registry=cast(Any, registry), identity=identity
    )


def test_release_verifier_handoff_rejects_still_active_historical_lease() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    identity = ReleaseEvidenceIdentity(
        issue_id="old-id",
        identifier="VERIFY-OLD",
        finalizer_identifier="APP-FINAL",
        role="verifier",
        cycle_generation="old-generation",
        retired=True,
        recorded_at=NOW,
        updated_at=NOW,
    )
    registry = SimpleNamespace(has_active_lease=lambda _issue_id: True)
    assert not orchestrator._release_verifier_handoff_is_durable(
        cfg=_cfg(), registry=cast(Any, registry), identity=identity
    )


def test_rehydrate_issue_flags_defers_unreadable_authority_and_clears_proven_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cancelled: list[str] = []
    subject._dispatch_state = SimpleNamespace(
        persisted_retry_attempts={"old-id": 2},
        turn_budget_exhausted=set(),
        cancel_pending_retry=lambda issue_id: cancelled.append(issue_id),
    )
    subject._paused_issue_ids = {"old-id"}
    subject._pause_reasons = {"old-id": "held"}
    subject._issue_debug = {}
    cleared: list[tuple[str, dict[str, Any]]] = []
    subject._clear_issue_flags = lambda issue_id, **fields: cleared.append(
        (issue_id, fields)
    )
    flag = SimpleNamespace(
        issue_id="old-id",
        budget_exhausted=True,
        retry_attempt=2,
        paused=False,
        pause_reason=None,
    )
    registry = SimpleNamespace(
        get_release_evidence_identity_by_issue_id=lambda _issue_id: (
            _ for _ in ()
        ).throw(sqlite3.OperationalError("locked"))
    )
    orchestrator._rehydrate_issue_flags(
        [flag], cfg=_cfg(), registry=cast(Any, registry)
    )
    assert "old-id" in subject._dispatch_state.turn_budget_exhausted

    identity = ReleaseEvidenceIdentity(
        issue_id="old-id",
        identifier="VERIFY-OLD",
        finalizer_identifier="APP-FINAL",
        role="verifier",
        cycle_generation="generation",
        retired=True,
        recorded_at=NOW,
        updated_at=NOW,
    )
    registry.get_release_evidence_identity_by_issue_id = lambda _issue_id: identity
    monkeypatch.setattr(
        orchestrator, "_release_verifier_handoff_is_durable", lambda **_kwargs: True
    )
    orchestrator._rehydrate_issue_flags(
        [flag], cfg=_cfg(), registry=cast(Any, registry)
    )
    assert cancelled == ["old-id"]
    assert "old-id" not in subject._paused_issue_ids
    assert cleared[-1] == ("old-id", {"retry_attempt": True, "paused": True})


def test_continuation_heartbeat_errors_and_reacquire_fail_open_are_observable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.agent.crash_continuation = True
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._run_registry_initialized = True
    subject._registry_error_count = 0
    subject._last_registry_error = None
    issue = _issue("APP-1")
    entry = _lease_entry(issue)
    registry = SimpleNamespace(
        heartbeat=lambda **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("locked")
        )
    )
    subject._run_registry = registry
    assert not orchestrator._heartbeat_run_lease(issue.id, cast(Any, entry))
    assert entry.lease_lost

    entry.lease_lost = True
    registry.heartbeat = lambda **_kwargs: False
    assert not orchestrator._heartbeat_run_lease(issue.id, cast(Any, entry))

    entry.lease_lost = False
    cfg.agent.crash_continuation = False
    subject._registry_guard = lambda op, fn, default: "" if op == "reacquire" else fn()
    assert orchestrator._heartbeat_run_lease(issue.id, cast(Any, entry))


def test_agent_process_identity_and_pid_sync_fail_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    entry = _lease_entry(issue)
    subject._run_registry = SimpleNamespace(
        get_run=lambda _run_id: (_ for _ in ()).throw(KeyError("missing"))
    )
    assert orchestrator._agent_pid_identity(cast(Any, entry)) is None
    subject._run_registry.get_run = lambda _run_id: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")
    )
    assert orchestrator._agent_pid_identity(cast(Any, entry)) is None

    cancelled: list[bool] = []
    task = SimpleNamespace(
        done=lambda: False,
        cancel=lambda: cancelled.append(True),
    )
    entry.worker_task = task
    subject._dispatch_state = SimpleNamespace(running={issue.id: entry})
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a, **_k: False)
    orchestrator._sync_backend_agent_pid(issue.id, 99)
    assert cancelled == [True]
    assert entry.cancelled_at is not None
    orchestrator._sync_backend_agent_pid("missing", 99)


def test_continuation_acquisition_fails_closed_on_race_error_and_registry_guard(
    tmp_path: Path,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.agent.crash_continuation = True
    issue = _issue("APP-1", state="Verify")
    registry = SimpleNamespace(
        latest_continuation_source=lambda **_kwargs: "source-run",
        acquire_continuation_run=lambda *_a, **_k: None,
    )
    subject._run_registry = registry
    subject._run_registry_initialized = True
    subject._registry_error_count = 0
    subject._last_registry_error = None
    assert (
        orchestrator._try_acquire_run_lease(
            cfg=cfg,
            issue=issue,
            workspace_path=tmp_path,
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
        )
        is None
    )

    registry.latest_continuation_source = lambda **_kwargs: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")
    )
    assert (
        orchestrator._try_acquire_run_lease(
            cfg=cfg,
            issue=issue,
            workspace_path=tmp_path,
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
        )
        is None
    )
    assert subject._last_registry_error is not None
    assert "acquire_continuation" in subject._last_registry_error

    registry.latest_continuation_source = lambda **_kwargs: None
    subject._registry_guard = lambda *_a: ""
    assert (
        orchestrator._try_acquire_run_lease(
            cfg=cfg,
            issue=issue,
            workspace_path=tmp_path,
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
        )
        is None
    )

    cfg.agent.crash_continuation = False
    subject._registry_guard = lambda *_a: ""
    acquisition = orchestrator._try_acquire_run_lease(
        cfg=cfg,
        issue=issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert acquisition is not None and acquisition.run_id == ""
    subject._registry_guard = lambda *_a: "fresh-run"
    acquisition = orchestrator._try_acquire_run_lease(
        cfg=cfg,
        issue=issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert acquisition is not None and acquisition.run_id == "fresh-run"
    subject._registry_guard = lambda *_a: None
    assert (
        orchestrator._try_acquire_run_lease(
            cfg=cfg,
            issue=issue,
            workspace_path=tmp_path,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
        )
        is None
    )


@pytest.mark.asyncio
async def test_start_and_tick_done_callbacks_surface_missing_config_without_stale_restart() -> (
    None
):
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._workflow_state = SimpleNamespace(
        current=lambda: None, reload=lambda: (None, None)
    )
    with pytest.raises(SymphonyError, match="workflow not loaded"):
        await orchestrator.start()

    stale = SimpleNamespace(cancelled=lambda: False, exception=lambda: RuntimeError())
    current = SimpleNamespace(cancelled=lambda: False, exception=lambda: None)
    subject._tick_task = current
    subject._stopping = False
    orchestrator._on_tick_task_done(cast(Any, stale))
    orchestrator._on_tick_task_done(cast(Any, current))


@pytest.mark.parametrize(
    ("record", "identity", "groups", "kill", "expected"),
    [
        (_record(backend_agent_pid=-1), None, [], None, None),
        (_record(backend_agent_pid=None), None, [], None, "no_backend_pid"),
        (_record(backend_agent_pid=10), None, [None], None, None),
        (_record(backend_agent_pid=10), None, [False], None, "not_found"),
        (_record(backend_agent_pid=10), "live", [True], None, None),
        (
            _record(backend_agent_pid=10, backend_process_identity="old"),
            "new",
            [True],
            None,
            "identity_mismatch",
        ),
        (
            _record(backend_agent_pid=10, backend_process_identity="same"),
            "same",
            [True, False],
            False,
            "not_found",
        ),
        (
            _record(backend_agent_pid=10, backend_process_identity="same"),
            "same",
            [True, True],
            False,
            None,
        ),
    ],
)
def test_reclaimed_process_cleanup_never_kills_an_unproven_incarnation(
    monkeypatch: pytest.MonkeyPatch,
    record: RunRecord,
    identity: str | None,
    groups: list[bool | None],
    kill: bool | None,
    expected: str | None,
) -> None:
    monkeypatch.setattr(core_module, "process_identity", lambda _pid: identity)
    group_values = iter(groups)
    monkeypatch.setattr(
        core_module, "process_group_exists", lambda _pid: next(group_values)
    )
    if kill is not None:
        monkeypatch.setattr(core_module, "kill_process_group", lambda _pid: kill)
    assert core_module._reclaim_kill_confirm(record) == expected


def test_reclaimed_process_cleanup_handles_kill_error_and_confirms_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(backend_agent_pid=10, backend_process_identity="same")
    monkeypatch.setattr(core_module, "process_identity", lambda _pid: "same")
    monkeypatch.setattr(core_module, "process_group_exists", lambda _pid: True)
    monkeypatch.setattr(
        core_module,
        "kill_process_group",
        lambda _pid: (_ for _ in ()).throw(OSError("denied")),
    )
    assert core_module._reclaim_kill_confirm(record) is None

    group_values = iter([True, True, False])
    monkeypatch.setattr(
        core_module, "process_group_exists", lambda _pid: next(group_values)
    )
    monkeypatch.setattr(core_module, "kill_process_group", lambda _pid: True)
    times = iter([0.0, 0.1])
    monkeypatch.setattr(core_module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(core_module.time, "sleep", lambda _seconds: None)
    assert core_module._reclaim_kill_confirm(record) == "killed"


def test_blocked_ticket_policy_extracts_current_episode_and_operator_state() -> None:
    cfg = _cfg(active=(), terminal=("Archive",))
    source = _issue(
        "APP / 1",
        labels=("Team Label", "blocked-fix", "Team Label"),
        description="""
## Blocked Fix
old request
## Blocked Fix Resolved
old result
## Blocked RCA
current request
## Fix Blocker
needs credentials
""",
    )
    assert core_module._blocked_rca_work_state(cfg) == "Todo"
    assert core_module._blocked_source_reopen_state(cfg) == "Todo"
    assert core_module._blocked_rca_labels(source) == [
        "blocked-fix",
        "source-app-1",
        "Team Label",
    ]
    assert core_module._blocked_rca_identifier_prefix(source) == "FIX-APP-1"
    assert core_module._blocked_rca_already_requested(source)
    assert not core_module._blocked_rca_current_episode_resolved(source)
    assert core_module._blocked_rca_requires_operator_intervention(source)
    assert core_module._blocked_rca_source_identifier(source) is None
    assert (
        core_module._blocked_rca_source_identifier(
            _issue(description="- Identifier: `SOURCE-1`")
        )
        == "SOURCE-1"
    )
    assert (
        core_module._blocked_rca_source_identifier(
            _issue(title="Fix and unblock SOURCE-2: repair")
        )
        == "SOURCE-2"
    )
    assert (
        core_module._blocked_rca_source_identifier(_issue(labels=("source-SOURCE-3",)))
        == "SOURCE-3"
    )
    assert core_module._human_review_done_state(cfg) is None
    assert core_module._blocked_rca_resolution_already_recorded(source)


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        (_issue(state="Done", description="Confirm Done"), False),
        (
            _issue(
                state="Human Review",
                labels=("blocked-rca",),
                description="Confirm Done",
            ),
            False,
        ),
        (_issue(state="Human Review", description=""), False),
        (
            _issue(
                state="Human Review",
                description="## Merge Failure\nconflict\n\nConfirm Done",
            ),
            False,
        ),
        (_issue(state="Human Review", description="Confirm Done"), True),
        (
            _issue(
                state="Human Review",
                description="## Merge Failure\nold\n## Unblock Note\nfixed",
            ),
            True,
        ),
    ],
)
def test_legacy_human_review_completion_requires_positive_unblocked_evidence(
    issue: Issue, expected: bool
) -> None:
    assert core_module._legacy_human_review_is_done(issue) is expected


@pytest.mark.asyncio
async def test_orchestrator_run_read_surfaces_distinguish_unavailable_missing_and_error() -> (
    None
):
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._run_registry = None
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    subject._registry_error_count = 0
    subject._last_registry_error = None
    assert await orchestrator.recent_runs() == ([], "run registry unavailable")
    assert await orchestrator.run_detail("run-1") == (
        None,
        "run registry unavailable",
    )
    assert await orchestrator.run_diagnostic("run-1") == (
        None,
        "run registry unavailable",
    )

    class Registry:
        def recent_runs(self, **_kwargs: Any) -> list[RunRecord]:
            return [_record()]

        def run_detail(self, run_id: str) -> dict[str, Any]:
            if run_id == "missing":
                raise KeyError(run_id)
            if run_id == "broken":
                raise sqlite3.OperationalError("locked")
            return {"run": {"run_id": run_id}, "events": []}

        def diagnostic_json(self, run_id: str) -> dict[str, Any]:
            if run_id == "missing":
                raise KeyError(run_id)
            if run_id == "broken":
                raise sqlite3.OperationalError("locked")
            return {"schema_version": 1, "run": {"run_id": run_id}, "events": []}

    subject._run_registry = Registry()
    subject._registry_guard = core_module.Orchestrator._registry_guard.__get__(
        orchestrator
    )
    rows, error = await orchestrator.recent_runs(
        issue_id="id-APP-1", limit=10, query="app", status="active", agent="codex"
    )
    assert error is None and rows[0]["run_id"] == "run-1"
    assert await orchestrator.run_detail("missing") == (None, None)
    detail, error = await orchestrator.run_detail("run-1")
    assert error is None and detail == {"run": {"run_id": "run-1"}, "events": []}
    assert await orchestrator.run_detail("broken") == (None, "run_detail: locked")
    assert await orchestrator.run_diagnostic("missing") == (None, None)
    diagnostic, error = await orchestrator.run_diagnostic("run-1")
    assert (
        error is None and diagnostic is not None and diagnostic["schema_version"] == 1
    )
    assert await orchestrator.run_diagnostic("broken") == (
        None,
        "run_diagnostic: locked",
    )


@pytest.mark.asyncio
async def test_orchestrator_run_reads_open_registry_lazily_and_report_guard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    subject._run_registry = None
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._last_registry_error = "recent_runs: locked"
    registry = SimpleNamespace(recent_runs=lambda **_kwargs: [])

    async def open_registry(_cfg: Any) -> None:
        subject._run_registry = registry

    monkeypatch.setattr(orchestrator, "_ensure_run_registry_async", open_registry)
    monkeypatch.setattr(orchestrator, "_registry_guard", lambda *_a: None)
    assert await orchestrator.recent_runs() == ([], "recent_runs: locked")

    class Registry:
        def run_detail(self, run_id: str) -> dict[str, Any]:
            return {"run": {"run_id": run_id}, "events": []}

        def diagnostic_json(self, run_id: str) -> dict[str, Any]:
            return {"schema_version": 1, "run": {"run_id": run_id}, "events": []}

    async def open_read_registry(_cfg: Any) -> None:
        subject._run_registry = Registry()

    monkeypatch.setattr(orchestrator, "_ensure_run_registry_async", open_read_registry)
    subject._run_registry = None
    detail, error = await orchestrator.run_detail("lazy-run")
    assert error is None and detail is not None
    subject._run_registry = None
    diagnostic, error = await orchestrator.run_diagnostic("lazy-run")
    assert error is None and diagnostic is not None


@pytest.mark.asyncio
async def test_orchestrator_observers_refresh_and_issue_snapshots_are_stable() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    observed: list[str] = []

    async def succeeds() -> None:
        observed.append("ok")

    async def fails() -> None:
        raise RuntimeError("observer failed")

    orchestrator._observers = [succeeds, fails]
    await orchestrator._notify_observers()
    assert observed == ["ok"]

    tick_event = SimpleNamespace(set=lambda: observed.append("refresh"))
    orchestrator._refresh_pending = False
    subject._tick_event = tick_event
    assert orchestrator.request_refresh() is False
    assert orchestrator.request_refresh() is True

    issue = _issue("APP-1", state="Verify")
    running = SimpleNamespace(
        issue=issue,
        workspace_path=Path("workspace/APP-1"),
        last_error=None,
    )
    subject._dispatch_state = SimpleNamespace(running={issue.id: running}, retry={})
    orchestrator._issue_debug = {}
    subject._running_row = lambda *_a: {"status": "running"}
    subject._retry_row = lambda _retry: {"status": "retrying"}
    assert orchestrator.iter_running_issues() == (issue,)
    assert orchestrator.find_resumable_issue_id("APP-1") == issue.id
    running_snapshot = orchestrator.issue_snapshot("APP-1")
    assert running_snapshot is not None and running_snapshot["status"] == "running"

    retry = SimpleNamespace(
        identifier="APP-2", attempt=2, kind="retry", error="rate limit"
    )
    subject._dispatch_state.running = {}
    subject._dispatch_state.retry = {"id-APP-2": retry}
    retry_snapshot = orchestrator.issue_snapshot("APP-2")
    assert retry_snapshot is not None and retry_snapshot["status"] == "retrying"
    assert orchestrator.issue_snapshot("missing") is None
    orchestrator._paused_issue_ids = {"APP-3"}
    assert orchestrator.find_resumable_issue_id("APP-2") == "id-APP-2"
    assert orchestrator.find_resumable_issue_id("APP-3") == "APP-3"
    assert orchestrator.find_resumable_issue_id("missing") is None


@pytest.mark.asyncio
async def test_skip_document_reports_reload_running_unknown_and_race_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._workflow_state = SimpleNamespace(
        current=lambda: None, reload=lambda: (None, "invalid workflow")
    )
    assert await orchestrator.skip_document("APP-1") == (
        False,
        "workflow config unavailable: invalid workflow",
    )

    cfg = _cfg()
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    monkeypatch.setattr(orchestrator, "find_running_issue_id", lambda _identifier: "id")
    assert "running worker" in (await orchestrator.skip_document("APP-1"))[1]

    monkeypatch.setattr(orchestrator, "find_running_issue_id", lambda _identifier: None)
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    assert await orchestrator.skip_document("APP-1") == (False, "unknown issue APP-1")

    issue = _issue("APP-1", state="Document")
    checks = iter([None, "id-APP-1"])
    monkeypatch.setattr(
        orchestrator, "find_running_issue_id", lambda _identifier: next(checks)
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: issue
    )
    assert "started running" in (await orchestrator.skip_document("APP-1"))[1]


def test_done_counter_load_and_persist_are_bounded_and_failure_tolerant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.workflow_path = tmp_path / "WORKFLOW.md"
    subject._done_count = 0
    path = orchestrator._done_count_path(cfg)
    path.parent.mkdir()
    path.write_text("not-json", encoding="utf-8")
    orchestrator._load_done_count(cfg)
    assert subject._done_count == 0
    path.write_text('{"done_count": 7}', encoding="utf-8")
    orchestrator._load_done_count(cfg)
    assert subject._done_count == 7

    fake_path = SimpleNamespace(
        parent=SimpleNamespace(
            mkdir=lambda **_kwargs: (_ for _ in ()).throw(OSError("denied"))
        )
    )
    monkeypatch.setattr(orchestrator, "_done_count_path", lambda _cfg: fake_path)
    orchestrator._persist_done_count(cfg)


@pytest.mark.asyncio
async def test_wiki_sweep_respects_disable_missing_root_and_scan_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.wiki = SimpleNamespace(sweep_every_n=0, root=tmp_path / "wiki")
    subject._done_count = 0
    subject._persist_done_count = lambda _cfg: None
    await orchestrator._maybe_run_wiki_sweep(cfg, identifier="APP-1")
    assert subject._done_count == 0

    cfg.wiki.sweep_every_n = 1
    cfg.wiki.root = None
    await orchestrator._maybe_run_wiki_sweep(cfg, identifier="APP-1")
    assert subject._done_count == 1

    cfg.wiki.root = tmp_path / "wiki"
    monkeypatch.setattr(
        core_module,
        "_wiki_sweep_run",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("scan failed")),
    )
    await orchestrator._maybe_run_wiki_sweep(cfg, identifier="APP-2")
    assert subject._done_count == 2

    report = SimpleNamespace(
        root=tmp_path / "wiki",
        duplicates=[],
        orphans=[],
        missing_files=[],
        stale_entries=[],
        mutations=[],
        is_clean=lambda: True,
    )
    monkeypatch.setattr(core_module, "_wiki_sweep_run", lambda *_a, **_k: report)
    await orchestrator._maybe_run_wiki_sweep(cfg, identifier="APP-3")
    assert subject._done_count == 3


@pytest.mark.asyncio
async def test_done_hook_and_merge_gate_no_manager_or_tracker_failure_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from symphony.utils.auto_merge import AutoMergeResult

    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._workspace_manager = None
    await orchestrator._after_done_then_remove_per_policy(
        _cfg(),
        tmp_path,
        identifier="APP-1",
        title="App",
        debug_target=None,
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_update_state",
        lambda *_a: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    result = AutoMergeResult(ok=False, status="conflict", detail="merge conflict")
    await orchestrator._block_done_ticket_for_merge_gate(
        _cfg(),
        _issue("APP-1", state="Done"),
        tmp_path,
        result=result,
        debug_target=None,
    )


@pytest.mark.asyncio
async def test_fifo_fallback_triages_rejects_conflicts_and_dispatches_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    candidates = [_issue(f"APP-{index}") for index in range(1, 5)]
    cfg = _cfg()
    subject._dispatch_state = SimpleNamespace(
        persisted_retry_attempts={candidates[3].id: 2}
    )
    monkeypatch.setattr(
        orchestrator,
        "_sort_with_wait_age_bump",
        lambda issues, _cfg, **_kwargs: issues,
    )
    monkeypatch.setattr(
        orchestrator,
        "_auto_triage_todo_if_actionable",
        lambda issue, _cfg: asyncio.sleep(0, result=issue is candidates[0]),
    )
    monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 1)
    monkeypatch.setattr(
        orchestrator,
        "_should_dispatch",
        lambda issue, _cfg: issue is not candidates[1],
    )
    monkeypatch.setattr(
        orchestrator,
        "_conflict_blocker",
        lambda issue: ("OTHER", {"src/shared.py"}) if issue is candidates[2] else None,
    )
    blocked: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_block_ticket_for_conflict",
        lambda _cfg, issue, _other, _overlap: asyncio.sleep(
            0, result=blocked.append(issue.identifier)
        ),
    )
    dispatched: list[tuple[str, int | None, str | None]] = []
    monkeypatch.setattr(
        orchestrator,
        "_dispatch",
        lambda issue, _cfg, **kwargs: dispatched.append(
            (issue.identifier, kwargs["attempt"], kwargs["attempt_kind"])
        ),
    )
    await orchestrator._dispatch_fifo_without_schedule_projection(candidates, cfg)
    assert blocked == ["APP-3"]
    assert dispatched == [("APP-4", 2, "retry")]


def test_orchestrator_attention_skips_resolved_blockers_and_routes_agent_by_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    cfg.agent.kind_for_state = lambda _state, _requested: "claude"
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._dispatch_state = SimpleNamespace(
        running={}, retry={}, turn_budget_exhausted=set()
    )
    orchestrator._blocked_rca_source_ids = set()
    orchestrator._lease_blocked = {}
    orchestrator._paused_issue_ids = set()
    orchestrator._pause_reasons = {}
    orchestrator._issue_debug = {}
    monkeypatch.setattr(
        orchestrator,
        "_issue_is_terminal",
        lambda issue: issue.state in cfg.tracker.terminal_states,
    )
    blocked = _issue(state="Blocked", description="## Blocked Fix\nrepair requested")
    blocked_attention = orchestrator.issue_attention(blocked)
    assert blocked_attention is not None
    assert blocked_attention["kind"] == "blocked_recovery_pending"

    dependent = _issue(
        blocked_by=(
            BlockerRef("done", "DONE-1", "Done"),
            BlockerRef("todo", "TODO-1", "Todo"),
        )
    )
    dependency_attention = orchestrator.issue_attention(dependent)
    assert dependency_attention is not None
    assert dependency_attention["kind"] == "blocked_dependency"
    assert (
        orchestrator._entry_agent_kind(
            cast(Any, SimpleNamespace(agent_kind="", issue=dependent))
        )
        == "claude"
    )


def test_orchestrator_dependency_and_health_surface_tracker_fetch_degradation() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    subject._workflow_state = SimpleNamespace(
        current=lambda: cfg, path=Path("WORKFLOW.md")
    )
    assert orchestrator.dependency_state_resolved("Done")
    assert not orchestrator.dependency_state_resolved("Blocked")

    orchestrator._tick_task = None
    orchestrator._stopping = False
    orchestrator._last_tick_completed_at = None
    orchestrator._consecutive_tick_failures = 0
    orchestrator._consecutive_candidate_fetch_failures = (
        core_module.TICK_DEGRADED_AFTER_CONSECUTIVE_FAILURES
    )
    orchestrator._last_registry_error = None
    orchestrator._service_instance_id = None
    orchestrator._tick_error_count = 0
    orchestrator._tick_loop_restarts = 0
    orchestrator._last_tick_error = None
    orchestrator._run_registry = None
    orchestrator._registry_error_count = 0
    subject._dispatch_state = SimpleNamespace(running={}, retry={})
    health = orchestrator.health()
    assert health["status"] == "degraded"
    assert health["degraded_reasons"] == ["tracker_fetch_failures"]


@pytest.mark.asyncio
async def test_worker_drain_reports_process_kill_failure_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    task = asyncio.create_task(waits())
    issue = _issue("APP-1")
    entry = SimpleNamespace(worker_task=task, agent_pgid=42, issue=issue)
    subject._dispatch_state = SimpleNamespace(running={issue.id: entry})
    monkeypatch.setattr(
        core_module.asyncio,
        "wait",
        lambda *_a, **_k: asyncio.sleep(0, result=(set(), {task})),
    )
    monkeypatch.setattr(
        core_module,
        "kill_process_group",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")),
    )
    monkeypatch.setattr(orchestrator, "_agent_pid_identity", lambda _entry: "birth")
    monkeypatch.setattr(orchestrator, "_entry_agent_kind", lambda _entry: "codex")
    finished: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orchestrator,
        "_finish_run_lease",
        lambda issue_id, _entry, reason: finished.append((issue_id, reason)),
    )
    await orchestrator._drain_worker_tasks()
    assert finished == [(issue.id, "shutdown_abandoned")]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_reclaimed_run_without_proven_cleanup_keeps_the_registry_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    monkeypatch.setattr(core_module, "_reclaim_kill_confirm", lambda _record: None)
    assert not await orchestrator._reap_and_finalize_reclaimed_run_async(
        cast(Any, SimpleNamespace()), _record()
    )
