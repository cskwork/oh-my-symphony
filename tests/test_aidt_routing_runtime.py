"""Runtime composition and orchestrator barrier contract for AIDT routing."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import symphony.aidt_routing as routing_facade
import symphony.aidt_routing.runtime as runtime_module
import symphony.orchestrator.core as core_module
from symphony.aidt_routing.contract import AidtRoutingResult
from symphony.issue import Issue
from symphony.jira_intake import JiraIntakeResult
from symphony.orchestrator import Orchestrator
from symphony.trackers.file import FileBoardTracker, write_ticket_atomic
from symphony.workflow import CodexConfig

from symphony.aidt_routing import filter_routing_candidates, run_aidt_routing

from .aidt_routing_support import service_config


_IMPORT_ORDER_PRELUDES = {
    "storage-first": """
import symphony.trackers.aidt_routes
assert "symphony.aidt_routing.runtime" not in sys.modules
""",
    "package-first": """
import symphony.aidt_routing
assert "symphony.aidt_routing.runtime" not in sys.modules
import symphony.trackers.aidt_routes
assert "symphony.aidt_routing.runtime" not in sys.modules
""",
    "public-runtime-first": """
from symphony.aidt_routing import filter_routing_candidates, run_aidt_routing
import symphony.trackers.aidt_routes
""",
    "core-first": """
import symphony.orchestrator.core as core
import symphony.aidt_routing as package
assert core.run_aidt_routing is package.run_aidt_routing
""",
}


def _enabled_config(tmp_path: Path, source_mode: str = "static_snapshot"):
    root = tmp_path / "aidt"
    root.mkdir()
    raw = {
        "aidt_routing": {
            "enabled": True,
            "source_mode": source_mode,
            "aidt_root": str(root),
            "minimum_confidence": 90,
            "states": {
                "ready": "Ready",
                "review": "Human Review",
                "coordinator": "Coordinating",
            },
            "services": [],
        }
    }
    config = service_config(tmp_path / "board", raw)
    return replace(
        config,
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
    )


def _jira_card(board_root: Path, identifier: str) -> None:
    write_ticket_atomic(
        board_root / f"{identifier}.md",
        {
            "id": identifier,
            "identifier": identifier,
            "title": identifier,
            "state": "Ready",
            "source": {"kind": "jira", "key": identifier},
        },
        "jira marker is display-only",
    )


def _routing_result(
    *,
    status: str = "success",
    allow_dispatch: bool = True,
    blocked: frozenset[str] = frozenset(),
    error_category: str | None = None,
) -> AidtRoutingResult:
    routed = int(status == "success")
    review = int(status == "review")
    return AidtRoutingResult(
        enabled=True,
        global_allow_dispatch=allow_dispatch,
        blocked_identifiers=blocked,
        routed_count=routed,
        review_count=review,
        child_count=max(0, len(blocked) - routed - review),
        failure_count=int(status == "failure"),
        status=status,
        error_category=error_category,
    )


def _issue(identifier: str) -> Issue:
    return Issue(
        id=identifier,
        identifier=identifier,
        title=identifier,
        description="",
        priority=1,
        state="Ready",
    )


class _StaticState:
    def __init__(self, config: Any, error: Exception | None = None) -> None:
        self.path = config.workflow_path
        self.config = config
        self.error = error

    def reload(self):
        return self.config, self.error

    def current(self):
        return self.config


async def _noop_async(*_args: object) -> None:
    return None


class _RuntimeCompositionFakes:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        self.catalog = object()
        monkeypatch.setattr(runtime_module, "observe_catalog", self.observe)
        monkeypatch.setattr(runtime_module, "resolve_card", self.resolve)
        monkeypatch.setattr(runtime_module, "apply_route_resolutions", self.apply)
        monkeypatch.setattr(runtime_module, "recheck_catalog", self.recheck)

    def observe(self, *_args: object, **_kwargs: object) -> object:
        self.events.append("observe")
        return self.catalog

    def resolve(
        self,
        front: dict[str, object],
        *_args: object,
        **_kwargs: object,
    ) -> object:
        identifier = str(front["id"])
        self.events.append(f"resolve:{identifier}")
        return SimpleNamespace(
            routed=identifier == "A20-1",
            blocked_identifiers=frozenset(
                {identifier, f"{identifier}--viewer-api"}
            ),
            children=(object(),),
            retained=(),
        )

    def apply(
        self,
        _board: object,
        resolutions: tuple[object, ...],
        **kwargs: object,
    ) -> object:
        self.events.append(f"apply:{len(resolutions)}")
        precommit = kwargs["precommit_hook"]
        assert callable(precommit)
        precommit()
        return SimpleNamespace(changed=4, partial_apply=False)

    def recheck(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("recheck")


def _isolate_tick(
    orchestrator: Orchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core_module, "validate_for_dispatch", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_ensure_run_registry", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_heartbeat_running_leases", lambda: None)
    monkeypatch.setattr(orchestrator, "_reconcile_running", _noop_async)
    monkeypatch.setattr(
        orchestrator,
        "_auto_normalize_legacy_human_review_done",
        _noop_async,
    )
    monkeypatch.setattr(
        orchestrator,
        "_auto_reopen_sources_from_resolved_rcas",
        _noop_async,
    )
    monkeypatch.setattr(orchestrator, "_auto_recover_blocked_sources", _noop_async)
    monkeypatch.setattr(orchestrator, "_archive_sweep", _noop_async)
    monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 0)
    monkeypatch.setattr(
        orchestrator,
        "_maybe_schedule_continuous_improvement",
        lambda _cfg: None,
    )
    monkeypatch.setattr(orchestrator, "_notify_observers", _noop_async)


def test_disabled_routing_constructs_no_board_and_preserves_default_dispatch(
    tmp_path: Path,
) -> None:
    config = service_config(
        tmp_path / "board",
        {"aidt_routing": {"enabled": False, "ignored": object()}},
    )
    constructed = False

    def board_factory(_tracker: object) -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("disabled routing must not construct a board")

    result = run_aidt_routing(config, board_factory=board_factory)

    assert result.enabled is False
    assert result.global_allow_dispatch is True
    assert result.blocked_identifiers == frozenset()
    assert result.status == "disabled"
    assert constructed is False


def test_facade_exports_only_the_frozen_runtime_contract() -> None:
    assert routing_facade.__all__ == [
        "MAX_ALIASES_PER_SERVICE",
        "MAX_ANCHORS_PER_CATEGORY",
        "MAX_EVIDENCE_RECORDS",
        "MAX_SERVICES",
        "MAX_VALUE_BYTES",
        "AidtRoutingFailure",
        "AidtRoutingResult",
        "AidtRouteDispatchContract",
        "canonical_fingerprint",
        "filter_routing_candidates",
        "load_routing_settings",
        "load_route_dispatch_contract",
        "run_aidt_routing",
    ]


@pytest.mark.parametrize(
    "prelude",
    _IMPORT_ORDER_PRELUDES.values(),
    ids=_IMPORT_ORDER_PRELUDES,
)
def test_public_facade_is_import_order_independent_in_fresh_process(
    prelude: str,
) -> None:
    script = f"""
import sys
{prelude}
import symphony.aidt_routing as facade
from symphony.aidt_routing import filter_routing_candidates, run_aidt_routing
from symphony.aidt_routing.runtime import (
    filter_routing_candidates as runtime_filter,
    run_aidt_routing as runtime_run,
)
assert facade.filter_routing_candidates is filter_routing_candidates is runtime_filter
assert facade.run_aidt_routing is run_aidt_routing is runtime_run
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_enabled_empty_poll_reports_success(tmp_path: Path) -> None:
    config = _enabled_config(tmp_path)

    result = run_aidt_routing(
        config,
        intake_result=JiraIntakeResult(False, 0, 0),
    )

    assert result.status == "success"
    assert result.global_allow_dispatch is True
    assert result.routed_count == 0
    assert result.review_count == 0


def test_runtime_resolves_complete_poll_and_rechecks_catalog_under_batch_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _enabled_config(tmp_path)
    board = FileBoardTracker(config.tracker)
    _jira_card(board.board_root, "A20-2")
    _jira_card(board.board_root, "A20-1")
    fakes = _RuntimeCompositionFakes(monkeypatch)

    result = run_aidt_routing(
        config,
        intake_result=JiraIntakeResult(False, 0, 0),
        board_factory=lambda _tracker: board,
        precommit_hook=lambda: fakes.events.append("injected_precommit"),
    )

    assert fakes.events == [
        "observe",
        "resolve:A20-1",
        "resolve:A20-2",
        "apply:2",
        "injected_precommit",
        "recheck",
    ]
    assert result.global_allow_dispatch is True
    assert result.routed_count == 1
    assert result.review_count == 1
    assert result.child_count == 2
    assert result.status == "review"
    assert result.blocked_identifiers == frozenset(
        {
            "A20-1",
            "A20-1--viewer-api",
            "A20-2",
            "A20-2--viewer-api",
        }
    )


@pytest.mark.parametrize(
    ("source_mode", "intake_result", "expected"),
    [
        ("same_tick_jira", None, "intake_unavailable"),
        ("same_tick_jira", JiraIntakeResult(False, 0, 0), "intake_unavailable"),
        ("static_snapshot", JiraIntakeResult(True, 0, 0), "source_mode_invalid"),
        ("static_snapshot", None, "source_mode_invalid"),
    ],
)
def test_source_mode_coupling_fails_before_catalog_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_mode: str,
    intake_result: JiraIntakeResult | None,
    expected: str,
) -> None:
    config = _enabled_config(tmp_path, source_mode)
    observed = False

    def observe(*_args: object, **_kwargs: object) -> object:
        nonlocal observed
        observed = True
        raise AssertionError("coupling failure must precede catalog observation")

    monkeypatch.setattr(runtime_module, "observe_catalog", observe)

    result = run_aidt_routing(config, intake_result=intake_result)

    assert result.global_allow_dispatch is False
    assert result.error_category == expected
    assert observed is False


def test_bounded_coordinator_scan_fails_before_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _enabled_config(tmp_path)
    _jira_card(config.tracker.board_root, "A20-1")  # type: ignore[arg-type]
    _jira_card(config.tracker.board_root, "A20-2")  # type: ignore[arg-type]
    monkeypatch.setattr(runtime_module, "MAX_COORDINATORS", 1)
    monkeypatch.setattr(
        runtime_module,
        "observe_catalog",
        lambda *_args, **_kwargs: pytest.fail("observation must not start"),
    )

    result = run_aidt_routing(
        config,
        intake_result=JiraIntakeResult(False, 0, 0),
    )

    assert result.global_allow_dispatch is False
    assert result.error_category == "batch_limit"


def test_runtime_sanitizes_unexpected_failures_in_result_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _enabled_config(tmp_path)

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("TOP-SECRET /private/catalog")

    monkeypatch.setattr(runtime_module, "observe_catalog", explode)

    result = run_aidt_routing(
        config,
        intake_result=JiraIntakeResult(False, 0, 0),
    )

    rendered = repr(result) + caplog.text
    assert result.error_category == "internal_error"
    assert result.error_ref is None
    assert "TOP-SECRET" not in rendered
    assert "/private/catalog" not in rendered


def test_candidate_filter_preserves_unrelated_order() -> None:
    candidates = [_issue("BLOCKED-1"), _issue("LOCAL-2"), _issue("LOCAL-1")]
    before = [issue.__dict__.copy() for issue in candidates]

    filtered = filter_routing_candidates(candidates, frozenset({"BLOCKED-1"}))

    assert [item.identifier for item in filtered] == ["LOCAL-2", "LOCAL-1"]
    assert [issue.__dict__ for issue in candidates] == before


@pytest.mark.asyncio
async def test_core_failure_stops_before_normalization_and_candidate_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = Orchestrator(_StaticState(_enabled_config(tmp_path)))  # type: ignore[arg-type]
    _isolate_tick(orchestrator, monkeypatch)
    normalized = False
    fetched = False

    async def normalize(_cfg: object) -> None:
        nonlocal normalized
        normalized = True

    async def fetch(_cfg: object) -> list[Issue]:
        nonlocal fetched
        fetched = True
        return []

    async def intake(_cfg: object):
        return core_module.JiraIntakePoll(True, "failure", None)

    monkeypatch.setattr(orchestrator, "_poll_jira_intake", intake)
    monkeypatch.setattr(orchestrator, "_auto_normalize_legacy_human_review_done", normalize)
    monkeypatch.setattr(orchestrator, "_fetch_candidates", fetch)
    monkeypatch.setattr(
        core_module,
        "run_aidt_routing",
        lambda *_args, **_kwargs: _routing_result(
            status="failure",
            allow_dispatch=False,
            error_category="intake_unavailable",
        ),
    )

    await orchestrator._on_tick()

    health = orchestrator.health()
    assert normalized is False
    assert fetched is False
    assert health["aidt_routing"]["last_error"] == "intake_unavailable"
    assert "aidt_routing_failure" in health["degraded_reasons"]


class _HostileRoutingValue:
    def __init__(self) -> None:
        self.repr_calls = 0

    def __repr__(self) -> str:
        self.repr_calls += 1
        return "TOP-SECRET-OBJECT-/private/payload"


class _HostileTickProbe:
    def __init__(self) -> None:
        self.normalized = False
        self.fetched = False
        self.logged: list[tuple[str, dict[str, object]]] = []

    async def normalize(self, _cfg: object) -> None:
        self.normalized = True

    async def fetch(self, _cfg: object) -> list[Issue]:
        self.fetched = True
        return []

    async def intake(self, _cfg: object):
        result = JiraIntakeResult(False, 0, 0)
        return core_module.JiraIntakePoll(False, "disabled", result)

    def warning(self, event: str, **fields: object) -> None:
        self.logged.append((event, fields))


def _combined_hostile_result(hostile: object) -> AidtRoutingResult:
    return AidtRoutingResult(
        1,
        0,
        frozenset({"../../SECRET-CARD"}),
        "TOP-SECRET-/private/count",
        -7,
        hostile,
        501,
        "failure",
        "internal_error",
        {"payload": "SECRET"},
    )


def _assert_hostile_tick_is_canonical(
    result: AidtRoutingResult,
    hostile: _HostileRoutingValue,
    probe: _HostileTickProbe,
    health: dict[str, object],
) -> None:
    rendered = repr(result) + repr(probe.logged) + repr(health)
    assert probe.normalized is False and probe.fetched is False
    assert hostile.repr_calls == 0
    assert "SECRET" not in rendered and "/private" not in rendered
    assert health["status"] == "failure"
    assert health["last_error"] == "internal_error"
    assert health["routed_count"] == 0
    assert health["review_count"] == 0
    assert health["child_count"] == 0
    assert health["failure_count"] == 1
    assert probe.logged == [
        (
            "aidt_routing_failure",
            {
                "category": "internal_error",
                "ref": None,
                "routed_count": 0,
                "review_count": 0,
                "child_count": 0,
                "failure_count": 1,
                "consecutive_failures": 1,
            },
        )
    ]


@pytest.mark.asyncio
async def test_core_normalizes_combined_hostile_result_before_every_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = Orchestrator(_StaticState(_enabled_config(tmp_path)))  # type: ignore[arg-type]
    _isolate_tick(orchestrator, monkeypatch)
    hostile = _HostileRoutingValue()
    probe = _HostileTickProbe()
    malformed = _combined_hostile_result(hostile)
    monkeypatch.setattr(orchestrator, "_poll_jira_intake", probe.intake)
    monkeypatch.setattr(
        orchestrator,
        "_auto_normalize_legacy_human_review_done",
        probe.normalize,
    )
    monkeypatch.setattr(orchestrator, "_fetch_candidates", probe.fetch)
    monkeypatch.setattr(
        core_module,
        "run_aidt_routing",
        lambda *_args, **_kwargs: malformed,
    )
    monkeypatch.setattr(core_module.log, "warning", probe.warning)

    await orchestrator._on_tick()

    health = orchestrator.health()["aidt_routing"]
    _assert_hostile_tick_is_canonical(malformed, hostile, probe, health)


@pytest.mark.asyncio
async def test_core_success_filters_route_managed_candidates_in_original_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = Orchestrator(_StaticState(_enabled_config(tmp_path)))  # type: ignore[arg-type]
    _isolate_tick(orchestrator, monkeypatch)
    seen: list[str] = []

    async def intake(_cfg: object):
        result = JiraIntakeResult(False, 0, 0)
        return core_module.JiraIntakePoll(False, "disabled", result)

    async def fetch(_cfg: object) -> list[Issue]:
        return [_issue("A20-1"), _issue("LOCAL-2"), _issue("LOCAL-1")]

    def capture(candidates: list[Issue], _cfg: object) -> list[Issue]:
        seen.extend(item.identifier for item in candidates)
        return []

    monkeypatch.setattr(orchestrator, "_poll_jira_intake", intake)
    monkeypatch.setattr(orchestrator, "_fetch_candidates", fetch)
    monkeypatch.setattr(orchestrator, "_sort_with_wait_age_bump", capture)
    monkeypatch.setattr(
        core_module,
        "run_aidt_routing",
        lambda *_args, **_kwargs: _routing_result(
            status="review",
            blocked=frozenset({"A20-1"}),
        ),
    )

    await orchestrator._on_tick()

    assert seen == ["LOCAL-2", "LOCAL-1"]
    health = orchestrator.health()["aidt_routing"]
    assert health["status"] == "review"
    assert health["review_count"] == 1
    assert health["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_enabled_last_good_reload_failure_stops_with_sanitized_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = _StaticState(_enabled_config(tmp_path), RuntimeError("TOP-SECRET reload"))
    orchestrator = Orchestrator(state)  # type: ignore[arg-type]
    _isolate_tick(orchestrator, monkeypatch)
    called = False

    def routing(*_args: object, **_kwargs: object) -> AidtRoutingResult:
        nonlocal called
        called = True
        raise AssertionError("reload failure must stop before routing")

    monkeypatch.setattr(core_module, "run_aidt_routing", routing)

    await orchestrator._on_tick()

    health = orchestrator.health()
    assert called is False
    assert health["aidt_routing"]["last_error"] == "workflow_reload_error"
    assert "TOP-SECRET" not in caplog.text


def test_health_transitions_are_exact_and_failure_retains_last_success(
    tmp_path: Path,
) -> None:
    orchestrator = Orchestrator(_StaticState(_enabled_config(tmp_path)))  # type: ignore[arg-type]
    initial = orchestrator.health()["aidt_routing"]
    assert orchestrator.health()["counts"] == {"running": 0, "retrying": 0}
    assert initial == {
        "enabled": False,
        "status": "disabled",
        "last_success": None,
        "last_error": None,
        "routed_count": 0,
        "review_count": 0,
        "child_count": 0,
        "failure_count": 0,
        "consecutive_failures": 0,
    }

    orchestrator._apply_aidt_routing_result(_routing_result(status="review"))
    success = orchestrator.health()["aidt_routing"]
    orchestrator._apply_aidt_routing_result(
        _routing_result(
            status="failure",
            allow_dispatch=False,
            error_category="internal_error",
        )
    )
    failure = orchestrator.health()["aidt_routing"]

    assert success["last_success"] is not None
    assert failure["last_success"] == success["last_success"]
    assert failure["last_error"] == "internal_error"
    assert failure["failure_count"] == 1
    assert failure["consecutive_failures"] == 1

    orchestrator._apply_aidt_routing_result(
        AidtRoutingResult(False, True, frozenset(), 0, 0, 0, 0, "disabled")
    )
    disabled = orchestrator.health()["aidt_routing"]
    assert disabled["status"] == "disabled"
    assert disabled["last_error"] is None
    assert disabled["failure_count"] == 0
    assert disabled["consecutive_failures"] == 0
