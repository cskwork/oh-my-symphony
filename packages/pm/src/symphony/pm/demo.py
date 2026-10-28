"""Create a disposable, offline PM workspace without changing host settings."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from symphony import i18n, runtime_safety
from symphony.orchestrator.run_registry import RunRegistry
from symphony.trackers.file import FileBoardTracker
from symphony.utils.git_sandbox import resolve_git_common_dir
from symphony.workflow import WorkflowState
from symphony.workflow.config import ServiceConfig
from .application import PmApplication
from .approval import PmApprovalService
from .documents import PmDocumentRepository
from .exchange import WorkbookExchange
from .handoff import PmHandoffService
from .models import PmError, PmFinding, PmRequestContext, ValidationReport
from .openspec import OpenSpecAdapter, resolve_node

OWNER = "local-operator"
PROJECT_ID = "pm-demo"
LOCATOR = "team-leave-request"
RESOURCE_ROOT = Path(__file__).parent / "resources" / "demo"
_PLACEHOLDER = re.compile(r"\{\{ID:([a-z][a-z0-9_]*)\}\}")
_SEMANTIC = re.compile(
    r"<!-- symphony:(req|scenario|storyboard|contract|decision)=\{\{ID:([a-z][a-z0-9_]*)\}\} -->"
)


class DemoError(Exception):
    """A CLI failure; creation failures keep the claimed directory intact."""

    def __init__(
        self, message: str, *, exit_code: int = 1, findings: tuple[PmFinding, ...] = ()
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.findings = findings


@dataclass(frozen=True)
class DemoPreflight:
    path: Path | None
    lang: str


@dataclass(frozen=True)
class DemoWorkspace:
    directory: Path
    workflow_path: Path
    planning_path: Path
    change_id: str
    lang: str
    validation: ValidationReport


def preflight_demo(
    path: str | Path | None = None, lang: str | None = None
) -> DemoPreflight:
    """Check inputs without writes, so the CLI can bind before claiming DIR."""
    if lang is not None and lang not in {"en", "ko"}:
        raise DemoError("--lang must be en or ko", exit_code=2)
    if shutil.which("git") is None:
        raise DemoError("PM Workspace demo needs git on PATH", exit_code=2)
    try:
        resolve_node()
    except PmError as exc:
        raise DemoError(exc.message, exit_code=2) from exc
    candidate = Path(path).expanduser().resolve() if path is not None else None
    if candidate is not None:
        _check_directory(candidate)
    return DemoPreflight(candidate, lang or i18n.resolve_language("en"))


def _check_directory(candidate: Path) -> None:
    if candidate.exists() and not candidate.is_dir():
        raise DemoError(f"demo path is not a directory: {candidate}", exit_code=2)
    if candidate.exists() and any(candidate.iterdir()):
        raise DemoError(f"demo folder is not empty: {candidate}", exit_code=2)
    protected = runtime_safety.protected_source_common_dir()
    if protected is None:
        return
    ancestor = candidate
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    common = resolve_git_common_dir(ancestor)
    if candidate.is_relative_to(protected.parent) or (
        common is not None and common.resolve() == protected.resolve()
    ):
        raise DemoError(
            f"demo folder is inside the protected Symphony source checkout: {candidate}",
            exit_code=2,
        )


def claim_demo_directory(preflight: DemoPreflight) -> Path:
    """Claim exclusively after the caller has bound its listening socket."""
    directory = preflight.path
    try:
        if directory is None:
            directory = Path(tempfile.mkdtemp(prefix="symphony-pm-demo-")).resolve()
        else:
            # Recheck after the bind; do not touch a folder populated meanwhile.
            _check_directory(directory)
            directory.mkdir(parents=True, exist_ok=True)
        os.mkdir(directory / "project")
    except OSError as exc:
        raise DemoError(
            f"cannot claim demo folder {directory}: {exc}", exit_code=2
        ) from exc
    return directory


def _git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True
    )
    if result.returncode:
        raise DemoError(
            f"demo git setup failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _write_workflow(directory: Path, lang: str) -> Path:
    frontmatter = {
        "tracker": {
            "kind": "file",
            "board_root": "./kanban",
            "project_slug": PROJECT_ID,
            "active_states": ["Todo", "In Progress"],
            "terminal_states": ["Done", "Cancelled"],
        },
        "workspace": {"root": str(directory / "worker-workspaces")},
        "agent": {
            "kind": "codex",
            "max_concurrent_agents": 1,
            "auto_merge_on_done": False,
            "auto_merge_push_target": False,
        },
        "codex": {"command": f"{shlex.quote(sys.executable)} -m symphony.mock_codex"},
        "tui": {"language": lang},
        "pm": {
            "authoring": True,
            "exchange": True,
            "handoff": True,
            "assistance": False,
            "planning_workspace_root": str(directory / "planning"),
            "verification_procedures": {
                "demo-check": [sys.executable, "-c", "print('demo check passed')"]
            },
            "manual_verification_procedures": {
                "demo-review": {
                    "label": "Product review",
                    "instructions": "Review the form, inbox, and calendar, including errors and keyboard use.",
                    "report": "required",
                    "artifacts": "optional",
                },
            },
        },
    }
    workflow = directory / "project" / "WORKFLOW.md"
    workflow.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\nOffline PM demo.\n",
        encoding="utf-8",
    )
    return workflow


def _fill_placeholders(source: str, identities: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in identities:
            raise DemoError(
                f"demo content has no semantic target for placeholder {name}"
            )
        return identities[name]

    return _PLACEHOLDER.sub(replace, source)


def _seed(
    repository: PmDocumentRepository, change_id: str, lang: str
) -> PmRequestContext:
    repository.claim_workspace(change_id, repository.planning_workspace_root, OWNER)
    repository.create_change(
        change_id=change_id,
        locator=LOCATOR,
        title="Team leave request" if lang == "en" else "팀 휴가 신청",
        owner=OWNER,
    )

    def context() -> PmRequestContext:
        change = repository.get_change(
            PmRequestContext(PROJECT_ID, change_id), owner=OWNER
        )
        return PmRequestContext(PROJECT_ID, change_id, change.revision)

    repository.create_spec(
        context(),
        "team-calendar",
        "Team calendar" if lang == "en" else "팀 캘린더",
        owner=OWNER,
    )
    resources = RESOURCE_ROOT / lang
    sources = {
        document: (resources / filename).read_text(encoding="utf-8")
        for document, filename in (
            ("proposal", "proposal.md"),
            ("ux", "ux.md"),
            ("design", "design.md"),
            ("spec:requirements", "spec-requirements.md"),
            ("spec:team-calendar", "spec-team-calendar.md"),
        )
    }
    identities: dict[str, str] = {}
    for document, source in sources.items():
        operations = []
        for kind, name in _SEMANTIC.findall(source):
            if name in identities or any(item["title"] == name for item in operations):
                raise DemoError(f"demo content duplicates semantic target {name}")
            operations.append(
                {
                    "op": "create_semantic",
                    "kind": "requirement" if kind == "req" else kind,
                    "title": name,
                }
            )
        repository.patch_document(context(), document, operations, owner=OWNER)
        change = repository.get_change(context(), owner=OWNER)
        for operation in operations:
            identities[operation["title"]] = next(
                item.semantic_id
                for item in change.semantic_index
                if item.document_id == document and item.title == operation["title"]
            )
    for document, source in sources.items():
        repository.patch_document(
            context(),
            document,
            [
                {
                    "op": "replace_source",
                    "source": _fill_placeholders(source, identities),
                }
            ],
            owner=OWNER,
        )

    wbs = yaml.safe_load(
        _fill_placeholders(
            (resources / "wbs.yaml").read_text(encoding="utf-8"), identities
        )
    )
    work_items: dict[str, str] = {}

    def create(key: str, task: dict[str, Any]) -> None:
        result = repository.patch_wbs(
            context(), [{"op": "create_task", "task": task}], owner=OWNER
        )
        work_items[key] = result.impacted_ids[0]

    for group_order, group in enumerate(wbs["groups"]):
        create(
            group["key"],
            {"kind": "Group", "title": group["title"], "order": group_order},
        )
        for task_order, raw in enumerate(group["tasks"]):
            task = dict(raw)
            key = task.pop("key")
            task["parent_id"] = work_items[group["key"]]
            task["order"] = task_order
            task["dependencies"] = [
                {
                    "task_id": work_items[dependency["task"]],
                    "kind": dependency["kind"],
                    **(
                        {"justification": dependency["justification"]}
                        if "justification" in dependency
                        else {}
                    ),
                }
                for dependency in task.pop("depends_on")
            ]
            create(key, task)
    return context()


def create_demo(directory: Path, lang: str) -> DemoWorkspace:
    """Populate a claimed DIR, validate it, and close the seeding registry."""
    directory = directory.resolve()
    project = directory / "project"
    registry: RunRegistry | None = None
    try:
        if lang not in {"en", "ko"}:
            raise DemoError("--lang must be en or ko", exit_code=2)
        if not project.is_dir() or any(project.iterdir()):
            raise DemoError("demo creation requires an empty, claimed project folder")
        for name in ("git-hooks", "worker-workspaces"):
            (directory / name).mkdir()
        _git(project, "init", "--template=", "-q")
        for key, value in (
            ("user.name", "Symphony PM Demo"),
            ("user.email", "pm-demo@example.invalid"),
            ("core.hooksPath", str(directory / "git-hooks")),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
        ):
            _git(project, "config", "--local", key, value)
        (project / "kanban").mkdir()
        shutil.copytree(
            Path(__file__).parent / "resources" / "openspec" / "symphony-pm",
            project / "openspec" / "schemas" / "symphony-pm",
        )
        (project / "openspec" / "config.yaml").write_text(
            "schema: spec-driven\n", encoding="utf-8"
        )
        for relative in (
            "openspec/specs/.gitkeep",
            "openspec/changes/archive/.gitkeep",
        ):
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        workflow = _write_workflow(directory, lang)
        _git(project, "add", ".")
        _git(project, "commit", "-qm", "Initialize PM demo")
        planning = directory / "planning"
        _git(project, "worktree", "add", "-q", "-b", "pm-demo-planning", str(planning))
        registry = RunRegistry(project / ".symphony" / "state.db")
        repository = PmDocumentRepository(
            project_id=PROJECT_ID,
            project_root=project,
            registry=registry,
            planning_workspace_root=planning,
        )
        change_id = uuid.uuid4().hex
        ctx = _seed(repository, change_id, lang)
        config, error = WorkflowState(workflow).reload()
        if config is None:
            raise DemoError(f"demo workflow is invalid: {error}")
        adapter = OpenSpecAdapter()
        application = PmApplication(
            repository,
            PmApprovalService(repository, registry),
            PmHandoffService(repository, registry, FileBoardTracker(config.tracker)),
            adapter,
            WorkbookExchange(),
            project_root=project,
            flags={},
        )
        report = application.validate_change(ctx, OWNER)
        if not report.valid:
            raise DemoError("demo validation failed", findings=report.findings)
        return DemoWorkspace(directory, workflow, planning, change_id, lang, report)
    except (OSError, PmError, ValueError, KeyError, StopIteration) as exc:
        raise DemoError(f"demo creation failed: {exc}") from exc
    finally:
        if registry is not None:
            registry.close()


def check_demo_paths(config: ServiceConfig | None, directory: Path) -> None:
    """Check the exact config the caller will serve, after validation."""
    if config is None:
        raise DemoError("demo configuration is unavailable")
    for key, value in (
        ("tracker.board_root", config.tracker.board_root),
        ("workspace.root", config.workspace_root),
        ("pm.planning_workspace_root", config.pm.planning_workspace_root),
    ):
        if value is None or not Path(value).resolve().is_relative_to(
            directory.resolve()
        ):
            raise DemoError(
                f"demo_config_outside: the demo configuration points outside the demo folder ({key} = {value})"
            )
