"""Disposable Git and durable-record fixtures for provisioner RED tests."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from symphony.aidt_routing import load_route_dispatch_contract, observe_service_binding
from symphony.aidt_routing.contract import load_routing_settings
from symphony.aidt_routing.decision import resolve_card
from symphony.aidt_routing.dispatch import AidtRouteDispatchContract
from symphony.aidt_routing.git_objects import ObservedService
from symphony.aidt_worktree.contract import (
    AIDT_COMPLETION_AUTHORIZATION_SCHEMA,
    AidtWorktreeSettings,
    CompletionAuthorization,
    load_aidt_worktree_settings,
    stable_worktree_paths,
)
from symphony.aidt_worktree.git_state import (
    FETCH_ARGV,
    GIT_FETCH_TIMEOUT_SECONDS,
    GIT_STDERR_CAP,
    GIT_STDOUT_CAP,
    GitCommandResult,
    default_binary_runner,
    git_environment,
)
from symphony.aidt_worktree.manifest import (
    activate_registry,
    admit_attempt,
    initial_attempt_record,
    persist_attempt,
    read_attempt,
)
from symphony.aidt_worktree.provisioner import (
    ActiveCompletionLease,
    AidtProvisioningAdmission,
    AidtWorktreeProvisioner,
    PreparedAidtWorktree,
)
from symphony.jira_intake import build_source_snapshot
from symphony.trackers.aidt_routes import apply_route_resolutions
from symphony.trackers.file import (
    FileBoardTracker,
    parse_ticket_file,
    write_ticket_atomic,
)
from symphony.trackers.jira import JiraInboxIssue
from symphony.workflow import ServiceConfig

from tests.aidt_routing_support import (
    catalog_observation,
    frozen_git_repository,
    routing_config,
    service_config,
    service_definition,
)


IDENTIFIER = "A20-1188--viewer-api"
COORDINATOR = "A20-1188"
NOW = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
NOW_TEXT = "2026-07-21T01:02:03Z"


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run one local-only Git fixture command."""
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=check,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@dataclass(frozen=True)
class RepositoryFixture:
    aidt_root: Path
    service_root: Path
    bare_origin: Path
    old_base_sha: str
    base_sha: str


def _repository(root: Path) -> RepositoryFixture:
    frozen = frozen_git_repository(
        root,
        {
            ".gitignore": "ignored/\n",
            "pom.xml": "<project/>\n",
            "src/Route.java": "GET /v-api/learning routeSymbol\n",
            "src/Domain.java": "math learning center\n",
            "tracked.txt": "old production base\n",
        },
        unrelated_head=False,
    )
    checkout = frozen.checkout.resolve()
    old_base_sha = frozen.base_commit
    git(checkout, "branch", "-M", "aidt-prd")
    git(checkout, "remote", "add", "origin", "https://fixture.invalid/repository.git")

    (checkout / "tracked.txt").write_text("current production base\n", encoding="utf-8")
    git(checkout, "add", "tracked.txt")
    git(checkout, "commit", "-m", "current production base")
    base_sha = git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    git(checkout, "update-ref", "refs/remotes/origin/aidt-prd", base_sha)

    bare_origin = (root.parent / "viewer-api-origin.git").resolve()
    git(root.parent, "clone", "--bare", str(checkout), str(bare_origin))
    assert (
        git(bare_origin, "rev-parse", "refs/heads/aidt-prd").stdout.decode().strip()
        == base_sha
    )

    git(checkout, "checkout", "-b", "unrelated-head", old_base_sha)
    (checkout / "head-only.txt").write_text("unrelated root history\n", encoding="utf-8")
    git(checkout, "add", "head-only.txt")
    git(checkout, "commit", "-m", "unrelated root head")
    (checkout / "tracked.txt").write_text("dirty root state\n", encoding="utf-8")
    (checkout / "untracked.txt").write_text("untracked root state\n", encoding="utf-8")
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "state.txt").write_text("ignored root state\n", encoding="utf-8")
    return RepositoryFixture(root.resolve(), checkout, bare_origin, old_base_sha, base_sha)


def _config(tmp_path: Path, aidt_root: Path) -> ServiceConfig:
    board = tmp_path / "board"
    board.mkdir()
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    service = service_definition(
        routes=[
            {
                "id": "learning-route",
                "file": "src/Route.java",
                "method": "GET",
                "endpoint": "/v-api/learning",
                "symbols": ["routeSymbol"],
            }
        ],
        domains=[
            {
                "id": "learning-domain",
                "file": "src/Domain.java",
                "terms": ["math learning center"],
            }
        ],
    )
    raw = routing_config(aidt_root, [service])
    raw["aidt_worktree"] = {"enabled": True}
    config = service_config(board, raw)
    return replace(
        config,
        workflow_path=(tmp_path / "WORKFLOW.md").resolve(),
        workspace_root=workspace.resolve(),
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


def _source() -> dict[str, object]:
    return build_source_snapshot(
        JiraInboxIssue(
            key="A20-1188",
            summary="GET /v-api/learning routeSymbol",
            description="viewer-api math learning center change",
            issue_type="Bug",
            components=("viewer-api",),
            status="Ready",
            priority="High",
            updated="2026-07-20T00:00:00Z",
            url="https://example.atlassian.net/browse/A20-1188",
        )
    )


def _route_cards(
    config: ServiceConfig,
    binding: ObservedService,
) -> AidtRouteDispatchContract:
    settings = load_routing_settings(config)
    assert settings is not None
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "src/Route.java": binding.contents["src/Route.java"],
                "src/Domain.java": binding.contents["src/Domain.java"],
            }
        },
        revisions={"viewer-api": binding.checkout_revision},
        bindings={"viewer-api": binding.repository_binding_digest},
    )
    source = _source()
    resolution = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: NOW,
    )
    board = FileBoardTracker(config.tracker)
    write_ticket_atomic(
        board.board_root / "A20-1188.md",
        {
            "id": "A20-1188",
            "identifier": "A20-1188",
            "title": "coordinator",
            "state": "Ready",
            "source": source,
        },
        "coordinator notes",
    )
    apply_route_resolutions(board, [resolution])
    route = load_route_dispatch_contract(config, IDENTIFIER)
    repeated = load_route_dispatch_contract(config, IDENTIFIER)
    assert route is not None
    assert repeated == route
    assert route.checkout_revision == binding.checkout_revision
    assert route.repository_binding_digest == binding.repository_binding_digest
    assert route.branch == "fix/A20-1188"
    coordinator, _ = parse_ticket_file(board.board_root / f"{COORDINATOR}.md")
    child, _ = parse_ticket_file(board.board_root / f"{IDENTIFIER}.md")
    coordinator_route = coordinator["routing"]
    child_route = child["routing"]
    assert isinstance(coordinator_route, dict)
    assert isinstance(child_route, dict)
    assert coordinator_route["fingerprint"] == route.coordinator_fingerprint
    assert child_route["fingerprint"] == route.route_fingerprint
    assert child_route["coordinator_fingerprint"] == route.coordinator_fingerprint
    assert child_route["repository_binding_digest"] == route.repository_binding_digest
    assert coordinator_route["repository_bindings"] == {
        "viewer-api": route.repository_binding_digest
    }
    return route


class RecordingFetchRunner:
    """Execute local Git commands and simulate only the exact production fetch."""

    def __init__(
        self,
        checkout: Path,
        revision: str,
        bare_origin: Path,
        events: list[str],
    ) -> None:
        self.checkout = checkout
        self.fetch_revision = revision
        self.bare_origin = bare_origin
        self.events = events
        self.requests: list[tuple[str, ...]] = []
        self.fetch_contracts: list[tuple[Path, dict[str, str], float, int, int]] = []
        self.fetch_calls = 0
        self.overrides: dict[int, GitCommandResult | Exception] = {}
        self.command_hook: Callable[[tuple[str, ...]], None] | None = None

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        self.requests.append(argv)
        if self.command_hook is not None:
            self.command_hook(argv)
        if argv == FETCH_ARGV:
            self.events.append("git:fetch")
            index = self.fetch_calls
            self.fetch_calls += 1
            self.fetch_contracts.append(
                (cwd, dict(environment), timeout, stdout_cap, stderr_cap)
            )
            override = self.overrides.pop(index, None)
            if isinstance(override, Exception):
                raise override
            if override is not None:
                return override
            bare_revision = (
                git(self.bare_origin, "rev-parse", "refs/heads/aidt-prd")
                .stdout.decode()
                .strip()
            )
            assert bare_revision == self.fetch_revision
            git(self.checkout, "update-ref", "refs/remotes/origin/aidt-prd", self.fetch_revision)
            return GitCommandResult(0, b"", b"")
        if "worktree" in argv and "add" in argv:
            self.events.append("git:add")
        if "worktree" in argv and "remove" in argv:
            self.events.append("git:remove")
        return default_binary_runner(
            argv, cwd, environment, timeout, stdout_cap, stderr_cap
        )

    def assert_exact_fetch_contract(self) -> None:
        assert self.fetch_contracts
        for cwd, environment, timeout, stdout_cap, stderr_cap in self.fetch_contracts:
            assert cwd == self.checkout
            assert environment == dict(git_environment())
            assert timeout == GIT_FETCH_TIMEOUT_SECONDS
            assert stdout_cap == GIT_STDOUT_CAP
            assert stderr_cap == GIT_STDERR_CAP

    def assert_no_forbidden_commands(self) -> None:
        forbidden = {"reset", "rebase", "switch", "checkout", "prune", "-D", "--force"}
        for argv in self.requests:
            assert not forbidden.intersection(argv)
            if "fetch" in argv:
                assert argv == FETCH_ARGV

    def count(self, operation: str) -> int:
        return self.events.count(f"git:{operation}")


class ExactTestAuthority:
    """Deterministic test capability; production remains deny-all."""

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[CompletionAuthorization, object, object, object]] = []

    def verify(
        self,
        authorization: CompletionAuthorization,
        lease: object,
        manifest: object,
        route: object,
    ) -> bool:
        self.calls.append((authorization, lease, manifest, route))
        return self.allowed and authorization.issued_at == NOW_TEXT


@dataclass
class ProvisionerFixture:
    root: Path
    aidt_root: Path
    service_root: Path
    bare_origin: Path
    old_base_sha: str
    base_sha: str
    config: ServiceConfig
    settings: AidtWorktreeSettings
    route: AidtRouteDispatchContract
    binding: ObservedService
    events: list[str]
    runner: RecordingFetchRunner

    @classmethod
    def create(cls, tmp_path: Path) -> "ProvisionerFixture":
        aidt_root = tmp_path / "aidt"
        repository = _repository(aidt_root)
        config = _config(tmp_path, aidt_root)
        settings = load_aidt_worktree_settings(config)
        assert settings is not None
        routing = load_routing_settings(config)
        assert routing is not None
        binding = observe_service_binding(routing, "viewer-api")
        assert binding.checkout_revision == repository.base_sha
        route = _route_cards(config, binding)
        git(
            repository.service_root,
            "update-ref",
            "refs/remotes/origin/aidt-prd",
            repository.old_base_sha,
        )
        events: list[str] = []
        runner = RecordingFetchRunner(
            repository.service_root,
            repository.base_sha,
            repository.bare_origin,
            events,
        )
        activate_registry(settings.paths, settings.workflow_identity, NOW_TEXT)
        return cls(
            root=tmp_path,
            aidt_root=repository.aidt_root,
            service_root=repository.service_root,
            bare_origin=repository.bare_origin,
            old_base_sha=repository.old_base_sha,
            base_sha=repository.base_sha,
            config=config,
            settings=settings,
            route=route,
            binding=binding,
            events=events,
            runner=runner,
        )

    @property
    def tmp_path(self) -> Path:
        return self.root

    @property
    def checkout(self) -> Path:
        return self.service_root

    @property
    def revision(self) -> str:
        return self.base_sha

    @property
    def paths(self):
        return stable_worktree_paths(self.settings.workflow_path, IDENTIFIER)

    @property
    def workspace(self) -> Path:
        return (self.settings.workspace_root / IDENTIFIER).resolve()

    def initial_admission(self) -> AidtProvisioningAdmission:
        record = initial_attempt_record(
            IDENTIFIER, self.route.route_pair_digest, self.settings.workflow_generation, NOW
        )
        persist_attempt(self.paths.attempt, record, expected_revision=None)
        admitted = admit_attempt(
            self.paths,
            record.record_revision,
            self.route.route_pair_digest,
            self.settings.workflow_generation,
            NOW,
            scope_attested=True,
        )
        assert admitted.admitted and admitted.action == "provision"
        return AidtProvisioningAdmission(
            IDENTIFIER,
            self.settings.workflow_generation,
            self.route.route_pair_digest,
            admitted.record.record_revision,
            "provision",
        )

    def current_admission(self) -> AidtProvisioningAdmission:
        record = read_attempt(self.paths.attempt)
        action = "resume" if record.disposition == "ready" else "provision"
        return AidtProvisioningAdmission(
            IDENTIFIER,
            self.settings.workflow_generation,
            self.route.route_pair_digest,
            record.record_revision,
            action,
        )

    def load_route(self, _config: ServiceConfig, identifier: str):
        self.events.append("route")
        return load_route_dispatch_contract(self.config, identifier)

    def observe_binding(self, _settings: object, _service: str) -> ObservedService:
        self.events.append("binding")
        settings = load_routing_settings(self.config)
        assert settings is not None
        return observe_service_binding(settings, "viewer-api")

    def provisioner(
        self,
        *,
        route_loader: Callable[[ServiceConfig, str], AidtRouteDispatchContract | None] | None = None,
        binding_observer: Callable[[object, str], object] | None = None,
        authority: object | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> AidtWorktreeProvisioner:
        return AidtWorktreeProvisioner(
            self.config,
            self.settings,
            runner=self.runner,
            clock=lambda: NOW,
            route_loader=route_loader or self.load_route,
            binding_observer=binding_observer or self.observe_binding,
            completion_authority=authority or ExactTestAuthority(),  # type: ignore[arg-type]
            fault_hook=fault_hook or (lambda _seam: None),
        )

    def prepare_ready(self) -> PreparedAidtWorktree:
        return self.provisioner().prepare(self.initial_admission())

    def authorization(self, **overrides: Any) -> CompletionAuthorization:
        values: dict[str, Any] = {
            "schema": AIDT_COMPLETION_AUTHORIZATION_SCHEMA,
            "identifier": IDENTIFIER,
            "workflow_generation": self.settings.workflow_generation,
            "route_pair_digest": self.route.route_pair_digest,
            "ready_manifest_revision": 2,
            "issue_id": IDENTIFIER,
            "run_id": "1" * 32,
            "attempt_kind": "initial",
            "owning_lease_token": "1" * 32,
            "final_transition_identity": "2" * 64,
            "issuer": "aidt-stage-controller-v1",
            "issued_at": NOW_TEXT,
            "authorization_digest": "3" * 64,
        }
        values.update(overrides)
        return CompletionAuthorization(**values)

    def lease(self, **overrides: Any) -> ActiveCompletionLease:
        values: dict[str, Any] = {
            "identifier": IDENTIFIER,
            "issue_id": IDENTIFIER,
            "run_id": "1" * 32,
            "attempt_kind": "initial",
            "active": True,
            "competing_owner": False,
        }
        values.update(overrides)
        return ActiveCompletionLease(**values)
