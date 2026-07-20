"""Shared builders for the split AIDT routing tests."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from symphony.aidt_routing.contract import RoutingSettings, canonical_fingerprint
from symphony.aidt_routing.git_objects import CatalogObservation, ObservedService
from symphony.workflow import ServiceConfig, TrackerConfig, WorkflowState


@dataclass(frozen=True)
class FrozenGitRepository:
    root: Path
    checkout: Path
    base_commit: str
    head_commit: str


def service_config(board: Path, raw: dict[str, Any]) -> ServiceConfig:
    """Build the smallest real service configuration needed by routing tests."""
    base = WorkflowState(Path("/tmp/missing-routing-workflow.md"))
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Ready", "In Progress"),
        terminal_states=("Done",),
        board_root=board,
    )
    return ServiceConfig(
        workflow_path=base.path,
        poll_interval_ms=30_000,
        workspace_root=board / "workspaces",
        tracker=tracker,
        hooks=SimpleNamespace(),
        agent=SimpleNamespace(kind="codex"),
        codex=SimpleNamespace(),
        claude=SimpleNamespace(),
        gemini=SimpleNamespace(),
        pi=SimpleNamespace(),
        server=SimpleNamespace(),
        raw=raw,
    )  # type: ignore[arg-type]


def service_definition(
    service_id: str = "viewer-api",
    *,
    checkout: str | None = None,
    enabled: bool = True,
    aliases: list[str] | None = None,
    context: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    domains: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one closed-schema service catalog entry."""
    return {
        "id": service_id,
        "checkout": checkout or service_id,
        "kind": "backend",
        "enabled": enabled,
        "markers": ["pom.xml"],
        "component_aliases": aliases or [service_id],
        "context_anchors": context or [],
        "route_anchors": routes or [],
        "domain_anchors": domains or [],
    }


def routing_config(root: Path, services: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the complete enabled routing configuration."""
    return {
        "aidt_routing": {
            "enabled": True,
            "source_mode": "static_snapshot",
            "aidt_root": str(root),
            "minimum_confidence": 90,
            "states": {
                "ready": "Ready",
                "review": "Human Review",
                "coordinator": "Coordinating",
            },
            "services": services,
        }
    }


def catalog_observation(
    settings: RoutingSettings,
    *,
    contents_by_service: Mapping[str, Mapping[str, str]],
    revisions: Mapping[str, str] | None = None,
    bindings: Mapping[str, str] | None = None,
) -> CatalogObservation:
    """Build a pure trusted-catalog value for decision tests."""
    revision_values = revisions or {}
    binding_values = bindings or {}
    services = tuple(
        ObservedService(
            service=service,
            revision_ref="refs/remotes/origin/aidt-prd",
            checkout_revision=revision_values.get(service.id, "a" * 40),
            repository_binding_digest=binding_values.get(
                service.id,
                canonical_fingerprint("test-binding-v1", service.id),
            ),
            contents=dict(contents_by_service.get(service.id, {})),
        )
        for service in settings.services
        if service.enabled
    )
    return CatalogObservation(services)


def git_command(cwd: Path, *args: str) -> str:
    """Run a local-only Git fixture command."""
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"},
    )
    return result.stdout.strip()


def frozen_git_repository(
    root: Path,
    files: Mapping[str, str],
    *,
    service_id: str = "viewer-api",
    unrelated_head: bool = True,
) -> FrozenGitRepository:
    """Freeze a local production ref, optionally moving HEAD elsewhere."""
    checkout = root / service_id
    checkout.mkdir(parents=True)
    git_command(checkout, "init", "-q")
    git_command(checkout, "config", "user.email", "routing@example.com")
    git_command(checkout, "config", "user.name", "Routing Test")
    for name, content in files.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git_command(checkout, "add", ".")
    git_command(checkout, "commit", "-qm", "production base")
    base_commit = git_command(checkout, "rev-parse", "HEAD")
    git_command(
        checkout,
        "update-ref",
        "refs/remotes/origin/aidt-prd",
        base_commit,
    )
    head_commit = base_commit
    if unrelated_head:
        git_command(checkout, "checkout", "-qb", "unrelated-head")
        (checkout / "head-only.txt").write_text("not routing input", encoding="utf-8")
        git_command(checkout, "add", "head-only.txt")
        git_command(checkout, "commit", "-qm", "unrelated head")
        head_commit = git_command(checkout, "rev-parse", "HEAD")
    return FrozenGitRepository(root, checkout, base_commit, head_commit)
