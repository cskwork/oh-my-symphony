"""Authenticated aiohttp route registration for PM Workspace."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextvars
import functools
import hashlib
import html
import io
import json
import re
import sqlite3
import subprocess
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import quote

from aiohttp import web

from . import host_tools
from .application import PmApplication
from .approval import PmApprovalService
from .documents import PmDocumentRepository
from .exchange import WorkbookExchange
from .handoff import PmHandoffService, _task_revision
from .models import ArchiveCandidate, ChangeView, DocumentView, ExportPayload, PmError, PmFinding, PmRequestContext, ValidationReport
from .openspec import OpenSpecAdapter
from .planning import PmPlanningClient
from .semantic import build_semantic_index
from .wbs import parse_wbs, patch_task_fields
from .verification import PmVerificationService, successful_run_provenance
from .coverage import PmCoverageService
from .identity import host_project_identity
from .locks import change_authority_lock
from symphony.orchestrator.run_registry import RunRegistry
from symphony.issue import PlanningRef
from symphony.trackers.file import FileBoardTracker

_CHANGE_ID = re.compile(r"^[0-9a-f]{32}$")
_T = TypeVar("_T")
PM_DEVELOPMENT_VERIFICATION_KEY: web.AppKey[bool] = web.AppKey(
    "symphony.pm.development_verification", bool
)
PM_DEMO_KEY: web.AppKey[Path] = web.AppKey("symphony.pm.demo", Path)


@dataclass
class _ActivePlanning:
    client: PmPlanningClient
    deadline: float
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_error: BaseException | None = None


def register_pm_routes(app: web.Application, ctx: Any, orchestrator: Any) -> None:
    active_planning: dict[str, _ActivePlanning] = {}
    cancelling_planning: set[str] = set()
    exchange_semaphores: dict[int, asyncio.Semaphore] = {}

    # glibc gives each allocating thread its own malloc arena and keeps freed
    # memory in it. On the default executor (CPUs + 4 threads) a 4-CPU Linux
    # server stayed ~60 MiB above idle after serving a 1,000-task plan; two PM
    # threads keep it near 21 MiB within the same latency budgets. Long-running
    # host commands (verification, acceptance, tool probes) stay on the default
    # executor so they cannot starve PM reads.
    pm_executor: list[ThreadPoolExecutor] = []

    async def to_pm_thread(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        if not pm_executor:
            workers = max(2, int(ctx.config().pm.exchange_concurrency) + 1)
            pm_executor.append(ThreadPoolExecutor(workers, thread_name_prefix="symphony-pm"))
        call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
        return await asyncio.get_running_loop().run_in_executor(pm_executor[0], call)

    async def shutdown_pm_executor(_app: web.Application) -> None:
        while pm_executor:
            pm_executor.pop().shutdown(wait=False)

    app.on_cleanup.append(shutdown_pm_executor)

    def storage_root(name: str) -> Path:
        return ctx.workflow_dir() / ".symphony" / "pm" / name

    def exchange_semaphore() -> asyncio.Semaphore:
        concurrency = max(1, int(ctx.config().pm.exchange_concurrency))
        return exchange_semaphores.setdefault(concurrency, asyncio.Semaphore(concurrency))

    def host_project_id() -> str:
        cfg = ctx.config()
        return host_project_identity(
            cfg.workflow_path, standalone_id=cfg.tracker.project_slug
        )

    def service() -> PmApplication:
        cfg = ctx.config()
        orchestrator._ensure_run_registry(cfg)
        registry = orchestrator._run_registry
        if registry is None:
            raise PmError("registry_unavailable", "PM registry is unavailable", status=503)
        project_id = host_project_id()
        planning_root = cfg.pm.planning_workspace_root
        if planning_root is None:
            raise PmError("capability_unavailable", "planning workspace root is not configured", status=503)
        repository = PmDocumentRepository(
            project_id=project_id, project_root=cfg.workflow_path.parent,
            registry=registry, planning_workspace_root=planning_root,
            max_nodes=cfg.pm.max_nodes, max_edges=cfg.pm.max_edges,
            max_text_chars=cfg.pm.max_text_chars,
        )
        openspec = OpenSpecAdapter(cfg.pm.openspec_executable)
        approval = PmApprovalService(
            repository, registry,
            external_validator=lambda root, locator: _openspec_readiness(
                openspec, root, locator
            ),
            verification_procedures=cfg.pm.verification_procedures,
            manual_verification_procedures=cfg.pm.manual_verification_procedures,
        )
        park_state = next(
            (state for state in cfg.tracker.terminal_states if state.casefold() in {"cancelled", "blocked", "superseded"}),
            cfg.tracker.archive_state,
        )
        handoff = PmHandoffService(
            repository, registry, ctx.file_tracker(), park_state=park_state
        )
        exchange = WorkbookExchange(
            max_workbook_bytes=cfg.pm.max_workbook_mb * 1024 * 1024,
            max_uncompressed_bytes=cfg.pm.max_uncompressed_mb * 1024 * 1024,
            max_zip_entries=cfg.pm.max_zip_entries,
            max_nodes=cfg.pm.max_nodes,
            max_edges=cfg.pm.max_edges,
            max_text_chars=cfg.pm.max_text_chars,
        )
        result = PmApplication(
            repository, approval, handoff,
            openspec, exchange,
            project_root=cfg.workflow_path.parent,
            flags={
                "authoring": cfg.pm.authoring, "exchange": cfg.pm.exchange,
                "assistance": cfg.pm.assistance, "handoff": cfg.pm.handoff,
            },
            archive_journal_root=ctx.workflow_dir() / ".symphony" / "pm" / "archive-journal",
            development_verification=bool(
                app.get(PM_DEVELOPMENT_VERIFICATION_KEY, False)
            ),
            demo=PM_DEMO_KEY in app,
        )
        return result

    async def run_service(work: Callable[[PmApplication], _T], pctx: PmRequestContext | None = None) -> _T:
        return await to_pm_thread(_with_thread_application, service(), work, pctx)

    def owner(request: web.Request) -> str:
        value = request.headers.get("X-Symphony-PM-Owner", "local-operator").strip()
        return value[:128] or "local-operator"

    def verification_service(pmapp: PmApplication | None = None) -> PmVerificationService:
        cfg = ctx.config()
        return PmVerificationService(
            (pmapp or service()).repository.registry, cfg.pm.verification_procedures,
            manual_procedures=cfg.pm.manual_verification_procedures,
            artifacts=ctx.artifacts(), timeout_seconds=cfg.pm.operation_timeout_ms / 1000,
            project_root=cfg.workflow_path.parent,
            successful_terminal_states=_successful_terminal_states(cfg),
        )

    async def body(request: web.Request) -> dict[str, Any]:
        if not request.body_exists:
            return {}
        value = await request.json()
        if not isinstance(value, dict):
            raise PmError("invalid_body", "body must be an object")
        return value

    async def import_body(request: web.Request) -> dict[str, Any]:
        """Read only the XLSX JSON upload above aiohttp's ordinary 1 MiB cap."""
        encoded_limit = ctx.config().pm.max_encoded_upload_mb * 1024 * 1024
        request_limit = encoded_limit + 64 * 1024
        if request.content_length is not None and request.content_length > request_limit:
            raise PmError("payload_too_large", "encoded upload exceeds limit", status=413)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > request_limit:
                raise PmError("payload_too_large", "encoded upload exceeds limit", status=413)
            chunks.append(chunk)
        try:
            # A multi-MB base64 upload decodes off the event loop.
            value = await to_pm_thread(json.loads, b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PmError("invalid_json", "request body is invalid") from exc
        if not isinstance(value, dict):
            raise PmError("invalid_body", "request body must be an object")
        return value

    def context(payload: dict[str, Any], request: web.Request, *, revision: bool = False, operation: bool = False) -> PmRequestContext:
        change_id = request.match_info.get("change_id")
        if not change_id or not _CHANGE_ID.fullmatch(change_id):
            raise PmError("invalid_request", "invalid change_id")
        return PmRequestContext.from_json(payload, change_id=change_id, require_revision=revision, require_operation=operation)

    def require_current_revision(change: ChangeView, pctx: PmRequestContext) -> None:
        if pctx.expected_revision != change.revision:
            raise PmError(
                "revision_conflict", "planning revision changed", status=409,
                details={"current_revision": change.revision},
            )

    async def guarded(handler, request: web.Request) -> web.StreamResponse:
        try:
            return await handler(request)
        except PmError as exc:
            return web.json_response(exc.as_json(), status=exc.status)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return web.json_response(PmError("invalid_body", "request body is invalid").as_json(), status=400)
        except sqlite3.OperationalError as exc:
            return web.json_response(
                PmError("registry_unavailable", f"PM registry schema is incomplete: {exc}", status=503).as_json(),
                status=503,
            )

    def wrap(handler):
        async def wrapped(request: web.Request):
            return await guarded(handler, request)
        return wrapped

    async def capabilities(_request: web.Request):
        tools = await asyncio.to_thread(host_tools.probe, ctx.config())
        return web.json_response(_json(await run_service(lambda pmapp: pmapp.capabilities(host_tools=tools))))

    async def list_changes(request: web.Request):
        if request.query.get("project_id") != host_project_id():
            raise PmError("wrong_project", "request project does not match", status=409)
        request_owner = owner(request)
        request_query = dict(request.query)
        def execute(pmapp: PmApplication):
            if request_query.get("project_id") != pmapp.repository.project_id:
                raise PmError("wrong_project", "request project does not match", status=409)
            return web.json_response({"changes": _json(pmapp.repository.list_changes(owner=request_owner))})

        return await run_service(execute)

    async def create_change(request: web.Request):
        payload = await body(request)
        if str(payload.get("project_id") or "") != host_project_id():
            raise PmError("wrong_project", "request project does not match", status=409)
        if payload.get("change_id") is not None:
            raise PmError(
                "server_minted_id_required",
                "change identities are assigned by the server",
                status=409,
            )
        workspace_path = str(payload.get("workspace_path") or "")
        locator = str(payload.get("locator") or "")
        if not workspace_path or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", locator):
            raise PmError("invalid_request", "workspace_path and valid locator are required")
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change_id = uuid.uuid4().hex
            claim_record = pmapp.claim_workspace(change_id, workspace_path, request_owner)
            try:
                change = pmapp.create_change(
                    change_id, locator, str(payload.get("title") or ""), request_owner
                )
            except Exception:
                pmapp.repository.registry.set_pm_workspace_state(
                    pmapp.repository.project_id, change_id,
                    fence=claim_record.fence, state="RELEASED",
                )
                raise
            return web.json_response(_json(change), status=201)

        return await run_service(execute)

    async def get_change(request: web.Request):
        payload = {"project_id": request.query.get("project_id", "")}
        pctx = context(payload, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            result = _json(change)
            workspace = pmapp.repository.registry.get_pm_workspace(pctx.project_id, pctx.change_id)
            result["workspace_state"] = workspace.state if workspace is not None else "UNAVAILABLE"
            result["approval_confirmation_token"] = (
                None if workspace is not None and workspace.state == "RELEASED"
                else pmapp.approval.confirmation_token(pctx, owner=request_owner)
            )
            return web.json_response(result)

        return await run_service(execute, pctx)

    async def claim(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            raw_inner = payload.get("payload")
            inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else payload
            pmapp._require("authoring")
            if pmapp.repository.registry.get_pm_workspace(pctx.project_id, pctx.change_id) is None:
                raise PmError(
                    "server_minted_id_required",
                    "new change identities are allocated by POST /changes",
                    status=409,
                )
            claim_result = pmapp.claim_workspace(pctx.change_id, str(inner.get("workspace_path") or ""), request_owner)
            return web.json_response(_json(claim_result))

        return await run_service(execute, pctx)

    async def repair(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            current = pmapp.repository.registry.get_pm_workspace(pctx.project_id, pctx.change_id)
            if current is None:
                raise PmError("workspace_unavailable", "planning workspace is not claimed", status=409)
            if current.state == "RELEASED":
                raise PmError("workspace_repair_required", "archived change identity is retired", status=409)
            raw_inner = payload.get("payload")
            inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else payload
            result = pmapp.repository.claim_workspace(
                pctx.change_id, Path(str(inner.get("workspace_path") or "")),
                request_owner, replace_existing=True,
            )
            return web.json_response(_json(result))

        return await run_service(execute, pctx)

    async def get_document(request: web.Request):
        pctx = context({"project_id": request.query.get("project_id", "")}, request)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            return web.json_response(_json(pmapp.repository.get_document(pctx, request_match_info["document_id"], owner=request_owner)))

        return await run_service(execute, pctx)

    async def get_baseline(request: web.Request):
        pctx = context({"project_id": request.query.get("project_id", "")}, request)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            capability = request_match_info["capability"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", capability):
                raise PmError("invalid_request", "invalid capability")
            claim_record = pmapp.repository._read_claim(
                pctx.change_id, owner=request_owner
            )
            path = Path(claim_record.workspace_path) / "openspec" / "specs" / capability / "spec.md"
            root = Path(claim_record.workspace_path).resolve()
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.exists():
                return web.json_response({"capability": capability, "exists": False, "source": ""})
            return web.json_response({"capability": capability, "exists": True, "source": resolved.read_text(encoding="utf-8")})

        return await run_service(execute, pctx)

    async def create_spec(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            result = pmapp.repository.create_spec(
                pctx, str(payload.get("capability") or ""),
                str(payload.get("title") or ""), owner=request_owner,
            )
            return web.json_response(result.as_json(), status=201)

        return await run_service(execute, pctx)

    async def patch_document(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            operations = payload.get("payload", {}).get("operations") if isinstance(payload.get("payload"), dict) else None
            if not isinstance(operations, list):
                raise PmError("invalid_request", "payload.operations must be a list")
            pmapp._require("authoring")
            result = pmapp.repository.patch_document(pctx, request_match_info["document_id"], operations, owner=request_owner)
            return web.json_response(result.as_json())

        return await run_service(execute, pctx)

    async def get_wbs(request: web.Request):
        pctx = context({"project_id": request.query.get("project_id", "")}, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            return web.json_response(_json(pmapp.repository.get_wbs(pctx, owner=request_owner)))

        return await run_service(execute, pctx)

    async def patch_wbs(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            operations = payload.get("payload", {}).get("operations") if isinstance(payload.get("payload"), dict) else None
            if not isinstance(operations, list):
                raise PmError("invalid_request", "payload.operations must be a list")
            pmapp._require("authoring")
            return web.json_response(pmapp.repository.patch_wbs(pctx, operations, owner=request_owner).as_json())

        return await run_service(execute, pctx)

    async def preview_wbs(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            if change.revision != pctx.expected_revision:
                raise PmError("revision_conflict", "planning revision changed", status=409)
            operations = payload.get("payload", {}).get("operations") if isinstance(payload.get("payload"), dict) else None
            if not isinstance(operations, list) or any(item.get("op") != "set_field" for item in operations if isinstance(item, dict)):
                raise PmError("invalid_request", "preview supports set_field operations")
            changes = []
            for operation in operations:
                if not isinstance(operation, dict) or not isinstance(operation.get("task_id"), str) or not isinstance(operation.get("field"), str):
                    raise PmError("invalid_request", "preview operation requires task_id and field")
                changes.append((operation["task_id"], operation["field"], operation.get("value")))
            document = pmapp.repository.get_document(pctx, "tasks", owner=request_owner)
            proposed = patch_task_fields(document.source, changes)
            parsed = parse_wbs(proposed)
            return web.json_response({"revision": change.revision, "findings": _json(parsed.findings), "tasks": _json(parsed.tasks)})

        return await run_service(execute, pctx)

    async def begin_external(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            claim_record = pmapp.repository._claim(pctx.change_id, owner=request_owner)
            return web.json_response(_json(pmapp.repository.begin_external_edit(pctx.change_id, fence=claim_record.fence, owner=request_owner)))

        return await run_service(execute, pctx)

    async def end_external(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            claim_record = pmapp.repository._claim(pctx.change_id, owner=request_owner)
            return web.json_response(_json(pmapp.repository.end_external_edit(pctx.change_id, fence=claim_record.fence, owner=request_owner)))

        return await run_service(execute, pctx)

    async def validate(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            return web.json_response(_json(pmapp.validate_change(pctx, request_owner)))

        return await run_service(execute, pctx)

    async def convert_legacy(request: web.Request):
        payload = await body(request)
        if request.match_info.get("change_id"):
            raise PmError("server_minted_id_required", "legacy conversion change identities are assigned by the server", status=409)
        if str(payload.get("project_id") or "") != host_project_id():
            raise PmError("wrong_project", "request project does not match", status=409)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            if request_match_info.get("change_id"):
                raise PmError(
                    "server_minted_id_required",
                    "legacy conversion change identities are assigned by the server",
                    status=409,
                )
            project_id = str(payload.get("project_id") or "")
            if project_id != pmapp.repository.project_id:
                raise PmError("wrong_project", "request project does not match", status=409)
            pctx = PmRequestContext(project_id, uuid.uuid4().hex)
            pmapp._require("handoff")
            result = pmapp.handoff.convert_legacy(
                pctx, str(payload.get("source_ticket_id") or ""),
                str(payload.get("workspace_path") or ""),
                str(payload.get("locator") or ""), str(payload.get("title") or ""),
                owner=request_owner,
            )
            return web.json_response(_json(result), status=201)

        return await run_service(execute)

    async def approve(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("authoring")
            manifest = pmapp.approval.approve(pctx, str(payload.get("confirmation_token") or ""), str(payload.get("reviewer_label") or request_owner), owner=request_owner)
            return web.json_response(_json(manifest), status=201)

        return await run_service(execute, pctx)

    async def handoff_preview(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("handoff")
            pmapp.repository._claim(pctx.change_id, owner=request_owner)
            return web.json_response(_json(pmapp.handoff.preview(pctx, owner=request_owner)))

        return await run_service(execute, pctx)

    async def handoff_publish(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            pmapp._require("handoff")
            pmapp.repository._claim(pctx.change_id, owner=request_owner)
            result = pmapp.handoff.publish(pctx, str(payload.get("preview_token") or ""), str(payload.get("confirmation_token") or ""), owner=request_owner)
            return web.json_response(_json(result), status=201)

        return await run_service(execute, pctx)

    async def create_export(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request)
        export_id = uuid.uuid4().hex
        export_root = storage_root("exports")
        format_name = str(payload.get("format") or payload.get("payload", {}).get("format") or "xlsx").lower()
        pmapp = service()
        request_owner = owner(request)

        def load_change() -> ChangeView:
            change = _read_change_snapshot(pmapp.repository, pctx, request_owner)
            if format_name == "xlsx":
                pmapp._require("exchange")
            return change

        change = await to_pm_thread(load_change)
        if format_name == "xlsx":
            async with exchange_semaphore():
                runtime = await to_pm_thread(
                    _with_thread_repository, pmapp.repository,
                    lambda repository: _export_runtime_projection(repository.registry, pmapp.handoff.tracker, change),
                )
                exported = await to_pm_thread(
                    pmapp.exchange.export, change, runtime, export_id=export_id
                )
        else:
            exported = await to_pm_thread(_render_export, change, format_name, pmapp.exchange)

        def store_export() -> None:
            export_root.mkdir(parents=True, exist_ok=True)
            suffix = Path(exported.filename).suffix
            (export_root / f"{export_id}{suffix}").write_bytes(exported.data)
            (export_root / f"{export_id}.snapshot.json").write_text(json.dumps(_json(change), sort_keys=True), encoding="utf-8")
            (export_root / f"{export_id}.json").write_text(json.dumps({"filename": exported.filename, "suffix": suffix, "content_type": exported.content_type, "metadata": exported.metadata}), encoding="utf-8")

        await to_pm_thread(store_export)
        return web.json_response({"export_id": export_id, "filename": exported.filename, "metadata": exported.metadata}, status=201)

    async def get_export(request: web.Request):
        export_id = request.match_info["export_id"]
        export_root = storage_root("exports")
        if not re.fullmatch(r"[0-9a-f]{32}", export_id):
            raise PmError("export_not_found", "unknown export", status=404)
        metadata_path = export_root / f"{export_id}.json"
        if not metadata_path.exists():
            raise PmError("export_not_found", "unknown export", status=404)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        path = export_root / f"{export_id}{metadata.get('suffix', '.xlsx')}"
        if not path.exists():
            raise PmError("export_not_found", "unknown export", status=404)
        return web.FileResponse(path, headers={"Content-Type": metadata.get("content_type", "application/octet-stream"), "Content-Disposition": f'attachment; filename="{metadata["filename"]}"'})

    async def preview_import(request: web.Request):
        payload = await import_body(request)
        pctx = context(payload, request)
        raw_upload = payload.get("payload")
        upload: dict[str, Any] = raw_upload if isinstance(raw_upload, dict) else {}
        filename = upload.get("filename")
        if (
            not isinstance(filename, str)
            or not filename.strip()
            or len(filename) > 255
            or filename != filename.strip()
            or "\x00" in filename
            or "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
            or not Path(filename).stem
            or Path(filename).suffix.casefold() != ".xlsx"
        ):
            raise PmError(
                "unsupported_media_type",
                "payload.filename must be a plain .xlsx filename",
                status=415,
            )
        encoded = upload.get("base64")
        if not isinstance(encoded, str):
            raise PmError("invalid_request", "payload.base64 is required")
        if len(encoded) > ctx.config().pm.max_encoded_upload_mb * 1024 * 1024:
            raise PmError("payload_too_large", "encoded upload exceeds limit", status=413)
        try:
            async with exchange_semaphore():
                data = await to_pm_thread(base64.b64decode, encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PmError("invalid_request", "payload.base64 is malformed") from exc
        if len(data) > ctx.config().pm.max_workbook_mb * 1024 * 1024:
            raise PmError(
                "payload_too_large", "decoded workbook exceeds limit", status=413
            )
        export_root = storage_root("exports")
        pmapp = service()
        request_owner = owner(request)
        await to_pm_thread(pmapp._require, "exchange")
        async with exchange_semaphore():
            metadata = await to_pm_thread(pmapp.exchange.inspect_metadata, data)
        export_id = metadata.get("export_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", export_id):
            raise PmError("trusted_base_missing", "workbook is not tied to a durable Symphony export", status=409)
        snapshot_path = export_root / f"{export_id}.snapshot.json"

        def load_bases() -> tuple[ChangeView, ChangeView]:
            if not snapshot_path.exists():
                raise PmError("trusted_base_missing", "trusted export snapshot is unavailable", status=409)
            trusted = _change_from_json(json.loads(snapshot_path.read_text(encoding="utf-8")))
            current = _read_change_snapshot(pmapp.repository, pctx, request_owner)
            return trusted, current

        trusted, current = await to_pm_thread(load_bases)
        async with exchange_semaphore():
            candidate = await to_pm_thread(
                pmapp.exchange.preview_import, data, trusted, current,
                filename=filename,
            )
        import_id = uuid.uuid4().hex
        result = _json(candidate)
        result["import_id"] = import_id
        result["change_id"] = pctx.change_id
        result["owner"] = request_owner
        import_root = storage_root("imports")

        def store_preview() -> None:
            import_root.mkdir(parents=True, exist_ok=True)
            (import_root / f"{import_id}.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")

        await to_pm_thread(store_preview)
        return web.json_response(result)

    async def apply_import(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            pmapp._require("exchange")
            import_id = request_match_info["import_id"]
            import_root = storage_root("imports")
            preview_path = import_root / f"{import_id}.json"
            if not re.fullmatch(r"[0-9a-f]{32}", import_id) or not preview_path.exists():
                raise PmError("import_not_found", "import preview is missing or expired", status=404)
            candidate = json.loads(preview_path.read_text(encoding="utf-8"))
            if candidate.get("change_id") != pctx.change_id or candidate.get("owner") != request_owner:
                raise PmError("import_not_found", "import preview belongs to another change", status=404)
            if candidate["preview_digest"] != str(payload.get("preview_digest") or ""):
                raise PmError("confirmation_mismatch", "import preview changed", status=409)
            if candidate["current_revision"] != pctx.expected_revision:
                raise PmError("revision_conflict", "planning revision changed after import preview", status=409)
            resolutions = payload.get("resolutions") or {}
            if not isinstance(resolutions, dict):
                raise PmError("invalid_request", "resolutions must be an object")
            conflict_keys = {f"{item['item_id']}:{item['field']}" for item in candidate.get("conflicts", [])}
            if any(key not in conflict_keys or value not in {"current", "imported"} for key, value in resolutions.items()):
                raise PmError("invalid_resolution", "conflict resolution is unknown or invalid")
            unresolved = [item for item in candidate.get("conflicts", []) if f"{item['item_id']}:{item['field']}" not in resolutions]
            if unresolved or any(item.get("severity") == "blocking" for item in candidate.get("findings", [])):
                raise PmError("import_conflict", "import conflicts must be resolved before apply", status=409, details={"unresolved": len(unresolved)})
            operations = []
            changes = list(candidate.get("changes", []))
            for conflict in candidate.get("conflicts", []):
                if resolutions.get(f"{conflict['item_id']}:{conflict['field']}") == "imported":
                    changes.append(conflict)
            for change in changes:
                if change["action"] == "add":
                    operations.append({"op": "add_task", "task_id": change["item_id"], "task": change["imported"]})
                elif change["action"] == "delete":
                    operations.append({"op": "delete_task", "task_id": change["item_id"]})
                else:
                    operations.append({"op": "set_field", "task_id": change["item_id"], "field": change["field"], "value": change["imported"]})
            registry = pmapp.repository.registry
            input_hash = "sha256:" + hashlib.sha256(json.dumps({"import_id": import_id, "preview_digest": candidate["preview_digest"], "resolutions": resolutions}, sort_keys=True).encode()).hexdigest()
            try:
                _, created = registry.create_pm_operation(
                    operation_id=pctx.client_operation_id or "", project_id=pctx.project_id,
                    change_id=pctx.change_id, kind="import_apply",
                    expected_revision=pctx.expected_revision, input_hash=input_hash,
                    owner_token=request_owner,
                )
            except ValueError as exc:
                raise PmError("invalid_operation", "operation id was reused for different input", status=409) from exc
            prepared: dict[str, Any]
            if not created:
                existing = _operation(registry, pctx.client_operation_id or "", project_id=pctx.project_id)
                if existing["state"] == "completed":
                    return web.json_response(existing["result"])
                prepared = existing["result"]
                if _import_recovery_matches(
                    pmapp.repository, pctx, prepared, owner=request_owner
                ):
                    current_change = pmapp.repository.get_change(pctx, owner=request_owner)
                    reconciled = {
                        "project_id": pctx.project_id, "change_id": pctx.change_id,
                        "previous_revision": pctx.expected_revision,
                        "revision": current_change.revision, "changed": ["tasks.md"],
                        "findings": [], "impacted_ids": sorted({item["item_id"] for item in candidate.get("changes", []) + candidate.get("conflicts", [])}),
                        "approval_invalidated": True, "operation_id": pctx.client_operation_id,
                        "import_id": import_id, "reconciled": True,
                    }
                    registry.finish_pm_operation(pctx.client_operation_id or "", state="completed", result=reconciled)
                    return web.json_response(reconciled)
                current_change = pmapp.repository.get_change(pctx, owner=request_owner)
                if current_change.revision != pctx.expected_revision:
                    registry.finish_pm_operation(pctx.client_operation_id or "", state="reconcile", error_code="import_reconcile")
                    raise PmError("import_reconcile", "import operation requires manual reconciliation", status=409)
                computed = {
                    "import_id": import_id,
                    **pmapp.repository.preview_wbs_patch(
                        pctx, operations, owner=request_owner
                    ),
                }
                if existing["state"] == "pending":
                    prepared = computed
                    registry.prepare_pm_operation(
                        pctx.client_operation_id or "", result=prepared
                    )
                elif prepared != computed:
                    registry.finish_pm_operation(
                        pctx.client_operation_id or "", state="reconcile",
                        error_code="import_reconcile",
                    )
                    raise PmError("import_reconcile", "import prepared state differs from current input", status=409)
            else:
                try:
                    prepared = {
                        "import_id": import_id,
                        **pmapp.repository.preview_wbs_patch(
                            pctx, operations, owner=request_owner
                        ),
                    }
                    registry.prepare_pm_operation(
                        pctx.client_operation_id or "", result=prepared
                    )
                except Exception as exc:
                    registry.finish_pm_operation(
                        pctx.client_operation_id or "", state="failed",
                        error_code=getattr(exc, "code", "import_prepare_failed"),
                    )
                    raise
            try:
                result = pmapp.repository.patch_wbs(pctx, operations, owner=request_owner)
                if not _import_recovery_matches(
                    pmapp.repository,
                    PmRequestContext(pctx.project_id, pctx.change_id),
                    prepared,
                    owner=request_owner,
                ):
                    registry.finish_pm_operation(
                        pctx.client_operation_id or "", state="reconcile",
                        error_code="import_postcondition",
                    )
                    raise PmError("import_reconcile", "import post-apply state differs from prepared manifest", status=409)
                response = result.as_json()
                response["import_id"] = import_id
                registry.finish_pm_operation(pctx.client_operation_id or "", state="completed", result=response)
                return web.json_response(response)
            except Exception as exc:
                registry.finish_pm_operation(pctx.client_operation_id or "", state="failed", error_code=getattr(exc, "code", "import_failed"))
                raise

        return await run_service(execute, pctx)

    async def planning_action(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        pmapp = service()
        request_owner = owner(request)
        def load_planning(thread_application: PmApplication):
            thread_application._require("assistance")
            return thread_application.repository.get_change(pctx, owner=request_owner)
        change = await run_service(load_planning, pctx)
        require_current_revision(change, pctx)
        action = str(payload.get("action") or "")
        legacy_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if not action and legacy_payload:
            action = "find_gaps"
            payload = {
                **payload,
                "document_id": legacy_payload.get("document_id", "proposal"),
                "instruction": legacy_payload.get("instruction", "Review the planning document."),
                "impacted_document_ids": [legacy_payload.get("document_id", "proposal")],
            }
        allowed_actions = {"draft_artifact", "refine_section", "find_gaps", "review_consistency", "explain_dependency", "propose_wbs", "update_impacted_artifacts"}
        if action not in allowed_actions:
            raise PmError("invalid_planning_action", "unknown planning action")
        document_id = str(payload.get("document_id") or "")
        impacted = tuple(dict.fromkeys(str(item) for item in payload.get("impacted_document_ids", []) if isinstance(item, str)))
        if len(impacted) > 8 or len(str(payload.get("instruction") or "")) > 8_192:
            raise PmError("invalid_planning_scope", "planning action scope exceeds limits")
        if action == "propose_wbs":
            impacted = ("tasks",)
        elif action in {"draft_artifact", "refine_section"}:
            impacted = (document_id,)
        elif action == "review_consistency" and len(impacted) < 2:
            raise PmError("invalid_planning_scope", "consistency review requires at least two artifacts")
        elif action == "update_impacted_artifacts" and (not document_id or not impacted):
            raise PmError("invalid_planning_scope", "impacted artifact update requires source and targets")
        elif action == "explain_dependency":
            dependency = payload.get("dependency")
            if not isinstance(dependency, dict) or set(dependency) != {"task_id", "predecessor_id"}:
                raise PmError("invalid_planning_scope", "dependency selection is required")
            wbs = await run_service(lambda thread_application: thread_application.repository.get_wbs(pctx, owner=request_owner), pctx)
            task = next((item for item in wbs.tasks if item.task_id == dependency["task_id"]), None)
            if task is None or str(dependency["predecessor_id"]) not in {item.task_id for item in task.dependencies}:
                raise PmError("invalid_planning_scope", "selected dependency is not current", status=409)
            impacted = ()
        if action == "refine_section":
            section_id = str(payload.get("section_id") or "")
            if not any(item.section_id == section_id and item.document_id == document_id for item in change.planning_sections):
                raise PmError("invalid_planning_scope", "selected section is not current", status=409)
        editable = {document.document_id: document for document in change.documents if document.editable}
        if any(item not in editable for item in impacted):
            raise PmError("invalid_planning_scope", "planning target is unavailable or read-only", status=409)
        snapshot = json.dumps({
            "action": action,
            "scope": {"document_id": document_id, "section_id": payload.get("section_id"), "dependency": payload.get("dependency"), "impacted_document_ids": impacted},
            "documents": {item: editable[item].source for item in impacted},
        }, ensure_ascii=False, sort_keys=True)
        operation_id = pctx.client_operation_id or ""
        registry = pmapp.repository.registry
        if any(
            active_id != operation_id
            and (entry.cleanup_error is not None or getattr(entry.client, "cleanup_uncertain", False))
            for active_id, entry in active_planning.items()
        ):
            raise PmError(
                "planning_cleanup_failed",
                "a prior planning process group is not confirmed stopped",
                status=503,
            )
        _, created = registry.create_pm_operation(
            operation_id=operation_id, project_id=pctx.project_id,
            change_id=pctx.change_id, kind="planning_proposal",
            expected_revision=pctx.expected_revision,
            input_hash="sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            owner_token=owner(request),
        )
        if not created:
            existing = _operation(registry, operation_id, project_id=pctx.project_id)
            return web.json_response(existing, status=200)
        cfg = ctx.config()
        client = PmPlanningClient(
            timeout_seconds=cfg.pm.operation_timeout_ms / 1000,
            max_total_tokens=cfg.agent.max_total_tokens,
        )
        entry = _ActivePlanning(client, asyncio.get_running_loop().time() + cfg.pm.operation_timeout_ms / 1000)
        active_planning[operation_id] = entry
        try:
            instruction = str(payload.get("instruction") or action)
            proposal = None
            raw_proposals: list[dict[str, Any]] = []
            preflight_findings: tuple[str, ...] = ()
            for proposal_attempt in range(2):
                entry.deadline = asyncio.get_running_loop().time() + cfg.pm.operation_timeout_ms / 1000
                proposal = await client.propose(
                    Path(tempfile_dir(ctx.workflow_dir())), snapshot, instruction
                )
                if operation_id in cancelling_planning:
                    raise PmError(
                        "operation_cancelled", "planning proposal was cancelled", status=409
                    )
                raw_proposals = list(proposal.proposals) if proposal.proposals else (
                    [{"document_id": impacted[0], "proposal_patch": proposal.proposal_patch}]
                    if impacted and proposal.proposal_patch else []
                )
                if any(item.get("document_id") not in impacted for item in raw_proposals):
                    raise PmError("invalid_proposal", "planning proposal targets an unselected document", status=409)
                proposal_document_ids = [
                    str(item.get("document_id") or "") for item in raw_proposals
                ]
                if len(proposal_document_ids) != len(set(proposal_document_ids)):
                    raise PmError(
                        "invalid_proposal",
                        "planning proposal targets one document more than once",
                        status=409,
                    )
                preflight_findings = _planning_proposal_preflight(
                    raw_proposals, editable
                )
                if not preflight_findings:
                    break
                if proposal_attempt == 0:
                    instruction = (
                        f"{instruction}\n\nThe previous proposal was structurally invalid "
                        f"({', '.join(preflight_findings)}). Return a corrected proposal. "
                        "For tasks.md, use only complete symphony-group/symphony-task "
                        "marker blocks with fenced managed YAML and no checkbox outside a "
                        "symphony-task block. Preserve the requested scope and placeholders."
                    )
            if preflight_findings:
                raise PmError(
                    "invalid_proposal",
                    "planning proposal is structurally invalid",
                    status=409,
                    details={"findings": list(preflight_findings)},
                )
            assert proposal is not None
            occupied_ids = {item.semantic_id for item in change.semantic_index}
            if "tasks" in impacted:
                current_wbs = await run_service(lambda thread_application: thread_application.repository.get_wbs(pctx, owner=request_owner), pctx)
                occupied_ids.update(item.task_id for item in current_wbs.tasks)
            if operation_id in cancelling_planning:
                raise PmError("operation_cancelled", "planning proposal was cancelled", status=409)
            reserved_ids, raw_proposals = _reserve_planning_ids(
                registry, operation_id, raw_proposals,
                occupied_ids=occupied_ids,
            )
            proposals = [
                {**item, "base_document_hash": editable[str(item["document_id"])].content_hash}
                for item in raw_proposals
            ]
            scope = {"document_id": document_id or None, "section_id": payload.get("section_id"), "dependency": payload.get("dependency"), "impacted_document_ids": list(impacted)}
            digest_payload = {"action": action, "scope": scope, "proposals": proposals, "reserved_ids": reserved_ids}
            result = {
                "action": action, "scope": scope, "explanation": proposal.explanation,
                "proposals": proposals, "blocked_instruction": proposal.blocked_instruction,
                "write_or_dispatch_performed": False, "reserved_ids": reserved_ids,
                "proposal_digest": "sha256:" + hashlib.sha256(json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "thread_id": proposal.thread_id, "turn_id": proposal.turn_id,
            }
            if not registry.finish_pm_operation(
                operation_id, state="completed", result=result
            ):
                terminal = _operation(
                    registry, operation_id, project_id=pctx.project_id
                )
                if terminal["state"] == "cancelled":
                    raise PmError(
                        "operation_cancelled",
                        "planning proposal was cancelled",
                        status=409,
                    )
                raise PmError(
                    "operation_not_ready",
                    "planning proposal reached another terminal state",
                    status=409,
                )
        except asyncio.CancelledError:
            registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
            if getattr(client, "cleanup_uncertain", False) and client.proposal_running:
                cancelling_planning.add(operation_id)
            else:
                registry.finish_pm_operation(operation_id, state="cancelled", error_code="cancelled")
            raise
        except Exception as exc:
            if getattr(client, "cleanup_uncertain", False) and client.proposal_running:
                cancelling_planning.add(operation_id)
                registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
                raise
            existing = _operation(registry, operation_id, project_id=pctx.project_id)
            if operation_id in cancelling_planning or existing["state"] == "cancelled":
                registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
                raise PmError("operation_cancelled", "planning proposal was cancelled", status=409) from exc
            registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
            registry.finish_pm_operation(operation_id, state="failed", error_code=getattr(exc, "code", "planning_failed"))
            raise
        finally:
            try:
                await client.stop()
                if getattr(client, "cleanup_uncertain", False):
                    raise PmError("planning_cleanup_failed", "planning process group could not be confirmed stopped", status=503)
                entry.cleanup_error = None
            except BaseException as exc:
                entry.cleanup_error = exc
                raise
            finally:
                entry.finished.set()
                if entry.cleanup_error is None and operation_id not in cancelling_planning:
                    active_planning.pop(operation_id, None)
        return web.json_response({"operation_id": operation_id, "state": "completed", "result": result}, status=201)

    async def apply_planning_action(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            pmapp._require("assistance")
            pmapp._require("authoring")
            proposal_id = request_match_info["operation_id"]
            operation = _operation(pmapp.repository.registry, proposal_id, project_id=pctx.project_id)
            if operation["state"] != "completed" or operation["kind"] != "planning_proposal" or operation["change_id"] != pctx.change_id:
                raise PmError("operation_not_ready", "planning proposal is not complete", status=409)
            if operation["expected_revision"] != pctx.expected_revision:
                raise PmError("revision_conflict", "planning proposal targets another revision", status=409)
            proposed = operation["result"] or {}
            if payload.get("proposal_digest") != proposed.get("proposal_digest"):
                raise PmError("confirmation_mismatch", "planning proposal confirmation is stale", status=409)
            proposals = proposed.get("proposals")
            if not isinstance(proposals, list) or not proposals:
                raise PmError("operation_not_ready", "planning result has no mutable proposal", status=409)
            reserved_ids = proposed.get("reserved_ids") or {}
            _assert_planning_reservations(
                pmapp.repository.registry, proposal_id, reserved_ids
            )
            sources: dict[str, str] = {}
            for item in proposals:
                document_id = str(item.get("document_id") or "")
                document = pmapp.repository.get_document(pctx, document_id, owner=request_owner)
                if document.content_hash != item.get("base_document_hash"):
                    raise PmError("proposal_conflict", "proposal base document changed", status=409)
                sources[document_id] = _apply_unified_diff(document.source, str(item.get("proposal_patch") or ""))
            result = pmapp.repository.patch_documents(
                pctx, sources, authorized_ids=tuple(reserved_ids.values()), owner=request_owner
            )
            pmapp.repository.registry._connect().execute(
                "UPDATE pm_planning_reserved_ids SET state='ACTIVE' WHERE operation_id=? AND state='RESERVED'",
                (proposal_id,),
            )
            return web.json_response(result.as_json())

        return await run_service(execute, pctx)

    async def get_operation(request: web.Request):
        return web.json_response(_operation(service().repository.registry, request.match_info["operation_id"], project_id=host_project_id()))

    async def cancel_operation(request: web.Request):
        operation_id = request.match_info["operation_id"]
        registry = service().repository.registry
        operation = _operation(
            registry, operation_id, project_id=host_project_id()
        )
        if operation["kind"] != "planning_proposal":
            raise PmError(
                "operation_not_cancellable",
                "only planning proposal operations can be cancelled",
                status=409,
            )
        terminal = operation["state"] in {"completed", "failed", "cancelled"}
        entry = active_planning.get(operation_id)
        if terminal and entry is None:
            registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
            return web.json_response(operation)
        if entry is not None:
            if not terminal:
                cancelling_planning.add(operation_id)
            try:
                if terminal and not entry.client.proposal_running:
                    await entry.client.stop()
                else:
                    finished_before_retry = entry.finished.is_set()
                    await entry.client.cancel_proposal()
                    if finished_before_retry:
                        entry.cleanup_error = None
                    if not entry.finished.is_set():
                        await asyncio.wait_for(
                            entry.finished.wait(), max(0, entry.deadline - asyncio.get_running_loop().time())
                        )
                    if entry.cleanup_error is not None:
                        raise PmError("planning_cleanup_failed", "planning process group could not be confirmed stopped", status=503) from entry.cleanup_error
                # A successful explicit retry supersedes an earlier cleanup error.
                entry.cleanup_error = None
            except asyncio.TimeoutError as exc:
                entry.cleanup_error = exc
                raise PmError("planning_cleanup_failed", "planning process group could not be confirmed stopped", status=503) from exc
            except BaseException as exc:
                entry.cleanup_error = exc
                raise
            if terminal:
                active_planning.pop(operation_id, None)
                cancelling_planning.discard(operation_id)
                return web.json_response(
                    _operation(registry, operation_id, project_id=host_project_id())
                )
        registry.finish_pm_operation(
            operation_id, state="cancelled", error_code="cancelled"
        )
        registry._connect().execute("UPDATE pm_planning_reserved_ids SET state='RETIRED' WHERE operation_id=? AND state='RESERVED'", (operation_id,))
        active_planning.pop(operation_id, None)
        cancelling_planning.discard(operation_id)
        return web.json_response(_operation(registry, operation_id, project_id=host_project_id()))

    async def archive_preview(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            preview = pmapp.preview_archive(pctx, request_owner)
            archive_id = uuid.uuid4().hex
            archive_root = storage_root("archives")
            archive_root.mkdir(parents=True, exist_ok=True)
            (archive_root / f"{archive_id}.json").write_text(json.dumps({**preview, "owner": request_owner}, sort_keys=True), encoding="utf-8")
            return web.json_response({"archive_id": archive_id, **preview})

        return await run_service(execute, pctx)

    async def archive_apply(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            archive_id = request_match_info["archive_id"]
            archive_root = storage_root("archives")
            path = archive_root / f"{archive_id}.json"
            if not re.fullmatch(r"[0-9a-f]{32}", archive_id) or not path.exists():
                raise PmError("archive_not_found", "archive preview is unavailable", status=404)
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored.get("owner") != request_owner:
                raise PmError("archive_not_found", "archive preview belongs to another owner", status=404)
            if payload.get("confirmation_token") != stored.get("confirmation_token"):
                raise PmError("confirmation_mismatch", "archive confirmation does not match", status=409)
            candidate = ArchiveCandidate(**{**stored["candidate"], "source_paths": tuple(stored["candidate"]["source_paths"]), "target_paths": tuple(stored["candidate"]["target_paths"]), "findings": tuple(PmFinding(**item) for item in stored["candidate"].get("findings", []))})
            result = pmapp.apply_archive(pctx, request_owner, candidate, str(payload.get("confirmation_token") or ""))
            return web.json_response(_json(result), status=201)

        return await run_service(execute, pctx)

    async def verify_task(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, operation=True)
        pmapp = service()
        await run_service(lambda thread_application: thread_application._require("handoff"), pctx)
        cfg = ctx.config()
        registry_path = pmapp.repository.registry.path
        raw_inner = payload.get("payload")
        inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else payload
        def execute_verification():
            thread_registry = RunRegistry(registry_path)
            try:
                verifier = PmVerificationService(
                    thread_registry, cfg.pm.verification_procedures,
                    artifacts=ctx.artifacts(), timeout_seconds=cfg.pm.operation_timeout_ms / 1000,
                    successful_terminal_states=_successful_terminal_states(cfg),
                )
                return verifier.run_procedure(
                    operation_id=pctx.client_operation_id or "",
                    project_id=pctx.project_id,
                    ticket_identifier=str(inner.get("ticket_identifier") or ""),
                    task_revision=str(inner.get("task_revision") or ""),
                    procedure_id=str(inner.get("procedure_id") or ""),
                    run_id=str(inner.get("run_id") or ""),
                    artifact_refs=tuple(str(item) for item in inner.get("artifact_refs", []) if isinstance(item, str)),
                )
            finally:
                thread_registry.close()
        receipt = await asyncio.to_thread(execute_verification)
        return web.json_response(_json(receipt), status=201)

    async def verification_receipts(request: web.Request):
        project_id = request.query.get("project_id", "")
        ticket = request.match_info["ticket_identifier"]
        revision = request.query.get("task_revision", "")
        commit = request.query.get("tested_commit", "")
        if project_id != host_project_id() or not revision or not commit:
            raise PmError("invalid_request", "project_id, task_revision, and tested_commit are required")
        rows = service().repository.registry.list_pm_verification_receipts(project_id, ticket, revision, commit)
        return web.json_response({"receipts": _json(rows)})

    async def manual_evidence_preview(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            require_current_revision(change, pctx)
            preview = verification_service(pmapp).preview_manual_evidence({
                **payload, "change_id": pctx.change_id, "submitted_by": request_owner
            })
            return web.json_response(_json(preview))

        return await run_service(execute, pctx)

    async def manual_evidence_confirm(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            require_current_revision(change, pctx)
            preview = verification_service(pmapp).preview_manual_evidence({
                **payload, "change_id": pctx.change_id, "submitted_by": request_owner
            })
            receipt, created = verification_service(pmapp).confirm_manual_evidence(
                preview, str(payload.get("confirmation_token") or ""),
                pctx.client_operation_id or "",
            )
            return web.json_response(_json(receipt), status=201 if created else 200)

        return await run_service(execute, pctx)

    async def evidence_report(request: web.Request):
        request_match_info = dict(request.match_info)
        request_query = dict(request.query)
        def execute(pmapp: PmApplication):
            receipt_id = request_match_info["receipt_id"]
            row = pmapp.repository.registry.get_pm_manual_verification_receipt(receipt_id)
            if row is None or not row.get("report_ref_json"):
                raise PmError("evidence_not_found", "manual report is unavailable", status=404)
            if request_query.get("project_id") != pmapp.repository.project_id or str(row["project_id"]) != pmapp.repository.project_id:
                raise PmError("wrong_project", "request project does not match", status=409)
            report = json.loads(str(row["report_ref_json"]))
            if report.get("ref_kind") != "git_blob":
                raise PmError("evidence_not_found", "manual report is unavailable", status=404)
            path = str(report.get("ref") or "")
            commit = str(row["tested_commit"])
            try:
                completed = subprocess.run(
                    ["git", "-C", str(pmapp.project_root), "show", f"{commit}:{path}"],
                    capture_output=True, check=False, timeout=ctx.config().pm.operation_timeout_ms / 1000,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PmError("evidence_not_found", "manual report bytes are unavailable", status=404) from exc
            digest = "sha256:" + hashlib.sha256(completed.stdout).hexdigest()
            if completed.returncode != 0 or digest != report.get("sha256"):
                raise PmError("evidence_not_found", "manual report bytes are unavailable", status=404)
            return web.Response(body=completed.stdout, content_type="application/octet-stream")

        return await run_service(execute)

    async def managed_tickets(request: web.Request):
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        request_query = dict(request.query)
        pctx = PmRequestContext(request_query.get("project_id", ""), request_match_info["change_id"])
        def execute(pmapp: PmApplication):
            project_id = request_query.get("project_id", "")
            if project_id != pmapp.repository.project_id:
                raise PmError("wrong_project", "request project does not match", status=409)
            change_id = request_match_info["change_id"]
            registry = pmapp.repository.registry
            change = pmapp.repository.get_change(PmRequestContext(project_id, change_id), owner=request_owner)
            tickets = []
            for item in registry.list_pm_managed_tickets(project_id):
                if item.change_id != change_id:
                    continue
                value = _json(item)
                value["verified_commit"] = registry.get_verified_pm_commit(project_id, item.ticket_identifier)
                value["acceptance"] = registry.get_active_pm_acceptance(project_id, item.ticket_identifier, item.task_revision)
                value["required_procedure_ids"] = sorted(registry.get_pm_required_procedures(project_id, item.ticket_identifier))
                required = registry.get_pm_required_procedures(project_id, item.ticket_identifier)
                value["allowed_procedures"] = [
                    ({"procedure_id": procedure_id, "kind": "native", "definition_digest": definition}
                     if isinstance(definition, str) else definition)
                    for procedure_id, definition in sorted(required.items())
                ]
                evidence_rows = [
                    ("native", dict(row))
                    for row in registry._connect().execute(
                        "SELECT * FROM pm_verification_records WHERE project_id=? "
                        "AND ticket_identifier=? AND task_revision=?",
                        (project_id, item.ticket_identifier, item.task_revision),
                    ).fetchall()
                ] + [
                    ("manual", dict(row))
                    for row in registry._connect().execute(
                        "SELECT * FROM pm_manual_verification_records WHERE project_id=? "
                        "AND ticket_identifier=? AND task_revision=?",
                        (project_id, item.ticket_identifier, item.task_revision),
                    ).fetchall()
                ]
                evidence_rows.sort(
                    key=lambda value: (
                        str(value[1].get("completed_at") or ""),
                        str(value[1].get("receipt_id") or ""),
                    )
                )
                value["evidence"] = []
                for provenance_kind, evidence in evidence_rows:
                    evidence["provenance_kind"] = provenance_kind
                    evidence["procedure_kind"] = str(
                        evidence.get("procedure_kind") or "native"
                    )
                    evidence["definition_digest"] = str(
                        evidence.get("definition_digest")
                        or evidence.get("command_digest") or ""
                    )
                    evidence["test_provenance"] = (
                        "manual_reported" if evidence.get("procedure_kind") == "manual"
                        else "host_executed"
                    )
                    report_json = evidence.pop("report_ref_json", None)
                    evidence["report_ref"] = json.loads(report_json) if report_json else None
                    if isinstance(evidence["report_ref"], dict):
                        evidence["report_ref"]["download_url"] = (
                            f"/api/v1/pm/evidence/{evidence['receipt_id']}/report"
                        )
                    evidence["artifact_refs"] = json.loads(
                        evidence.pop("artifact_refs_json", "[]") or "[]"
                    )
                    evidence.pop("input_hash", None)
                    value["evidence"].append(evidence)
                value.update(_ticket_code_evidence(
                    registry, project_id, item.ticket_identifier, item.task_revision,
                    change.base_git_sha, _successful_terminal_states(ctx.config()),
                ))
                tickets.append(value)
            return web.json_response({"tickets": _json(tickets)})

        return await run_service(execute, pctx)

    async def coverage_report(request: web.Request):
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        request_query = dict(request.query)
        pctx = PmRequestContext(request_query.get("project_id", ""), request_match_info["change_id"])
        def execute(pmapp: PmApplication):
            project_id = request_query.get("project_id", "")
            revision = request_query.get("revision", "")
            change_id = request_match_info["change_id"]
            change = pmapp.repository.get_change(PmRequestContext(project_id, change_id), owner=request_owner)
            if revision and revision != change.revision:
                raise PmError("revision_conflict", "coverage revision is stale", status=409)
            tasks = pmapp.repository.get_wbs(PmRequestContext(project_id, change_id), owner=request_owner).tasks
            return web.json_response(_json(PmCoverageService(
                pmapp.repository.registry,
                successful_terminal_states=tuple(ctx.config().tracker.terminal_states),
            ).build(change, tasks)))

        return await run_service(execute, pctx)

    async def waiver_preview(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            require_current_revision(change, pctx)
            result = PmCoverageService(pmapp.repository.registry).waiver_preview(
                change, str(payload.get("semantic_id") or ""), str(payload.get("reason") or ""),
                str(payload.get("reviewer_label") or request_owner),
            )
            return web.json_response(result)

        return await run_service(execute, pctx)

    async def waiver_create(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            require_current_revision(change, pctx)
            coverage = PmCoverageService(pmapp.repository.registry)
            preview = coverage.waiver_preview(
                change, str(payload.get("semantic_id") or ""), str(payload.get("reason") or ""),
                str(payload.get("reviewer_label") or request_owner),
            )
            row, created = coverage.create_waiver(
                preview, str(payload.get("confirmation_token") or ""), pctx.client_operation_id or ""
            )
            return web.json_response(_json(row), status=201 if created else 200)

        return await run_service(execute, pctx)

    async def waiver_revoke(request: web.Request):
        payload = await body(request)
        pctx = context(payload, request, revision=True, operation=True)
        request_owner = owner(request)
        request_match_info = dict(request.match_info)
        def execute(pmapp: PmApplication):
            change = pmapp.repository.get_change(pctx, owner=request_owner)
            require_current_revision(change, pctx)
            try:
                result = pmapp.repository.registry.revoke_pm_coverage_waiver(
                    request_match_info["waiver_id"], project_id=pctx.project_id,
                    change_id=pctx.change_id, revision=change.revision,
                    operation_id=pctx.client_operation_id or "",
                    reviewer=str(payload.get("reviewer_label") or request_owner),
                    reason=str(payload.get("reason") or "revoked"),
                    revoked_at=datetime.now(timezone.utc).isoformat(),
                )
            except ValueError as exc:
                raise PmError("operation_conflict", str(exc), status=409) from exc
            if result is None:
                raise PmError("waiver_not_found", "coverage waiver is unknown", status=404)
            row, _created = result
            return web.json_response(_json(row))

        return await run_service(execute, pctx)

    async def acceptance_preview(request: web.Request):
        if request.query.get("project_id", "") != host_project_id():
            raise PmError("wrong_project", "request project does not match", status=409)
        request_match_info = dict(request.match_info)
        request_query = dict(request.query)
        def execute(pmapp: PmApplication):
            project_id = request_query.get("project_id", "")
            if project_id != pmapp.repository.project_id:
                raise PmError("wrong_project", "request project does not match", status=409)
            preview = verification_service(pmapp).preview_acceptance(
                project_id=project_id, ticket_identifier=request_match_info["ticket_identifier"],
                task_revision=request_query.get("task_revision", ""),
                tested_commit=request_query.get("tested_commit", ""),
            )
            return web.json_response(_json(preview))

        return await run_service(execute)

    async def accept_verification(request: web.Request):
        payload = await body(request)
        ticket_identifier = request.match_info["ticket_identifier"]
        project_id = str(payload.get("project_id") or "")
        if project_id != host_project_id():
            raise PmError("wrong_project", "request project does not match", status=409)
        pmapp = service()
        cfg = ctx.config()
        registry_path = pmapp.repository.registry.path
        done_state = next(
            (state for state in cfg.tracker.terminal_states if state.casefold() == "done"),
            None,
        )
        tracker = ctx.file_tracker()

        def execute_acceptance():
            thread_registry = RunRegistry(registry_path)
            try:
                verifier = PmVerificationService(
                    thread_registry, cfg.pm.verification_procedures,
                    manual_procedures=cfg.pm.manual_verification_procedures,
                    timeout_seconds=cfg.pm.operation_timeout_ms / 1000,
                    project_root=cfg.workflow_path.parent,
                    successful_terminal_states=_successful_terminal_states(cfg),
                )
                preview = verifier.preview_acceptance(
                    project_id=project_id, ticket_identifier=ticket_identifier,
                    task_revision=str(payload.get("task_revision") or ""),
                    tested_commit=str(payload.get("tested_commit") or ""),
                )
                return verifier.accept(
                    preview=preview,
                    confirmation_token=str(payload.get("confirmation_token") or ""),
                    reviewer_label=str(payload.get("reviewer_label") or owner(request)),
                    complete_ticket=(
                        (lambda: tracker.update_fields(
                            ticket_identifier, state=done_state
                        ))
                        if done_state else None
                    ),
                )
            finally:
                thread_registry.close()

        record = await asyncio.to_thread(execute_acceptance)
        return web.json_response(_json(record), status=201)

    app.router.add_get("/api/v1/pm/capabilities", wrap(capabilities))
    app.router.add_get("/api/v1/pm/changes", wrap(list_changes))
    app.router.add_post("/api/v1/pm/changes", wrap(create_change))
    app.router.add_get("/api/v1/pm/changes/{change_id}", wrap(get_change))
    app.router.add_post("/api/v1/pm/changes/{change_id}/workspace/claim", wrap(claim))
    app.router.add_post("/api/v1/pm/changes/{change_id}/workspace/repair", wrap(repair))
    app.router.add_post("/api/v1/pm/changes/{change_id}/external-edit/begin", wrap(begin_external))
    app.router.add_post("/api/v1/pm/changes/{change_id}/external-edit/end", wrap(end_external))
    app.router.add_post("/api/v1/pm/changes/{change_id}/legacy-conversions", wrap(convert_legacy))
    app.router.add_post("/api/v1/pm/legacy-conversions", wrap(convert_legacy))
    app.router.add_get("/api/v1/pm/changes/{change_id}/documents/{document_id}", wrap(get_document))
    app.router.add_get("/api/v1/pm/changes/{change_id}/baselines/{capability}", wrap(get_baseline))
    app.router.add_post("/api/v1/pm/changes/{change_id}/specs", wrap(create_spec))
    app.router.add_patch("/api/v1/pm/changes/{change_id}/documents/{document_id}", wrap(patch_document))
    app.router.add_get("/api/v1/pm/changes/{change_id}/wbs", wrap(get_wbs))
    app.router.add_patch("/api/v1/pm/changes/{change_id}/wbs", wrap(patch_wbs))
    app.router.add_post("/api/v1/pm/changes/{change_id}/wbs/preview", wrap(preview_wbs))
    app.router.add_post("/api/v1/pm/changes/{change_id}/validate", wrap(validate))
    app.router.add_post("/api/v1/pm/changes/{change_id}/planning-actions", wrap(planning_action))
    app.router.add_post("/api/v1/pm/changes/{change_id}/planning-actions/{operation_id}/apply", wrap(apply_planning_action))
    app.router.add_get("/api/v1/pm/operations/{operation_id}", wrap(get_operation))
    app.router.add_post("/api/v1/pm/operations/{operation_id}/cancel", wrap(cancel_operation))
    app.router.add_post("/api/v1/pm/changes/{change_id}/archive/preview", wrap(archive_preview))
    app.router.add_post("/api/v1/pm/changes/{change_id}/archive/{archive_id}", wrap(archive_apply))
    app.router.add_post("/api/v1/pm/changes/{change_id}/approvals", wrap(approve))
    app.router.add_post("/api/v1/pm/changes/{change_id}/verification", wrap(verify_task))
    app.router.add_post("/api/v1/pm/changes/{change_id}/manual-evidence/preview", wrap(manual_evidence_preview))
    app.router.add_post("/api/v1/pm/changes/{change_id}/manual-evidence", wrap(manual_evidence_confirm))
    app.router.add_get("/api/v1/pm/evidence/{receipt_id}/report", wrap(evidence_report))
    app.router.add_get("/api/v1/pm/changes/{change_id}/managed-tickets", wrap(managed_tickets))
    app.router.add_get("/api/v1/pm/changes/{change_id}/coverage", wrap(coverage_report))
    app.router.add_post("/api/v1/pm/changes/{change_id}/coverage/waivers/preview", wrap(waiver_preview))
    app.router.add_post("/api/v1/pm/changes/{change_id}/coverage/waivers", wrap(waiver_create))
    app.router.add_post("/api/v1/pm/changes/{change_id}/coverage/waivers/{waiver_id}/revoke", wrap(waiver_revoke))
    app.router.add_get("/api/v1/pm/tickets/{ticket_identifier}/verification-receipts", wrap(verification_receipts))
    app.router.add_get("/api/v1/pm/tickets/{ticket_identifier}/acceptance/preview", wrap(acceptance_preview))
    app.router.add_post("/api/v1/pm/tickets/{ticket_identifier}/acceptance", wrap(accept_verification))
    app.router.add_post("/api/v1/pm/changes/{change_id}/handoffs/preview", wrap(handoff_preview))
    app.router.add_post("/api/v1/pm/changes/{change_id}/handoffs", wrap(handoff_publish))
    app.router.add_post("/api/v1/pm/changes/{change_id}/exports", wrap(create_export))
    app.router.add_get("/api/v1/pm/exports/{export_id}/file", wrap(get_export))
    app.router.add_post("/api/v1/pm/changes/{change_id}/imports/preview", wrap(preview_import))
    app.router.add_post("/api/v1/pm/changes/{change_id}/imports/{import_id}/apply", wrap(apply_import))


def _with_thread_application(
    application: PmApplication, work: Callable[[PmApplication], _T],
    pctx: PmRequestContext | None = None,
) -> _T:
    """Run a complete synchronous transaction with thread-owned PM services."""
    def execute(repository: PmDocumentRepository) -> _T:
        registry = repository.registry
        approval = PmApprovalService(
            repository, registry, application.approval.external_validator,
            verification_procedures=application.approval.verification_procedures,
            manual_verification_procedures=application.approval.manual_verification_procedures,
        )
        handoff = PmHandoffService(
            repository, registry, application.handoff.tracker,
            target_state=application.handoff.target_state,
            park_state=application.handoff.park_state,
        )
        thread_application = PmApplication(
            repository, approval, handoff, application.openspec, application.exchange,
            project_root=application.project_root, flags=application.flags,
            archive_journal_root=application.archive_journal_root,
            development_verification=application.development_verification,
            demo=application.demo,
        )
        with change_authority_lock(registry, pctx.project_id, pctx.change_id) if pctx else nullcontext():
            return work(thread_application)
    return _with_thread_repository(application.repository, execute)


def _with_thread_repository(
    repository: PmDocumentRepository,
    work: Callable[[PmDocumentRepository], _T],
) -> _T:
    """Run repository work in a worker thread on its own registry connection.

    The orchestrator's SQLite connection belongs to the event-loop thread, so
    a worker opens a private one, as verification and acceptance do.
    """
    thread_registry = RunRegistry(repository.registry.path)
    try:
        return work(PmDocumentRepository(
            project_id=repository.project_id,
            project_root=repository.project_root,
            registry=thread_registry,
            planning_workspace_root=repository.planning_workspace_root,
            max_nodes=repository.max_nodes,
            max_edges=repository.max_edges,
            max_text_chars=repository.max_text_chars,
        ))
    finally:
        thread_registry.close()


def _export_runtime_projection(
    registry: RunRegistry, tracker: FileBoardTracker, change: ChangeView,
) -> dict[str, dict[str, str]]:
    document = next((item for item in change.documents if item.document_id == "tasks"), None)
    revisions = {
        task.task_id: _task_revision(task) for task in parse_wbs(document.source).tasks
    } if document is not None else {}
    eligible = []
    approvals: dict[str, dict[str, Any] | None] = {}
    for mapping in registry.list_pm_managed_tickets(change.project_id):
        if (
            mapping.project_id != change.project_id or mapping.change_id != change.change_id
            or mapping.state != "PUBLISHED" or mapping.task_revision != revisions.get(mapping.task_id)
        ):
            continue
        scope = registry.get_pm_managed_ticket_scope(change.project_id, mapping.ticket_identifier)
        if scope is None or (
            scope["project_id"], scope["change_id"], scope["task_id"], scope["task_revision"],
            scope["handoff_id"], scope["deterministic_ticket_id"], scope["state"], scope["batch_state"],
        ) != (
            change.project_id, change.change_id, mapping.task_id, mapping.task_revision,
            mapping.handoff_id, mapping.ticket_identifier, "PUBLISHED", "PUBLISHED",
        ):
            continue
        approval_id = str(scope["approval_id"])
        if approval_id not in approvals:
            approvals[approval_id] = registry.get_pm_approval(approval_id)
        approval = approvals[approval_id]
        if approval is None or (
            approval["project_id"], approval["change_id"], approval["scope_hash"], approval["approval_id"],
        ) != (change.project_id, change.change_id, scope["scope_hash"], scope["snapshot_id"]):
            continue
        reference = PlanningRef(change.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, str(scope["snapshot_id"]))
        eligible.append((mapping, reference))
    if not eligible:
        return {}
    issues = {issue.identifier: issue for issue in tracker.scan_all()}
    runtime = {}
    for mapping, reference in sorted(eligible, key=lambda item: item[0].updated_at, reverse=True):
        issue = issues.get(mapping.ticket_identifier)
        if issue is not None and issue.planning_ref_state == "valid" and issue.planning_ref == reference:
            runtime.setdefault(mapping.task_id, {
                "state": issue.state, "link": f"/api/v1/issues/{quote(issue.identifier, safe='')}",
            })
    return runtime


def _read_change_snapshot(
    repository: PmDocumentRepository, pctx: PmRequestContext, request_owner: str,
) -> ChangeView:
    """Read the change an export or import preview is built from, off the loop.

    The read takes the change lock (shared) so a loop-thread writer mid-commit
    cannot hand a torn snapshot to the export's trusted base.
    """
    return _with_thread_repository(
        repository,
        lambda thread_repository: thread_repository.get_change(
            pctx, owner=request_owner, locked=True
        ),
    )


def _json(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def tempfile_dir(workflow_dir: Path) -> str:
    path = workflow_dir / ".symphony" / "pm" / "planning-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _change_from_json(value: dict[str, Any]) -> ChangeView:
    documents = tuple(DocumentView(**item) for item in value.get("documents", []))
    findings = tuple(
        PmFinding(**{**item, "path": tuple(item.get("path", []))})
        for item in value.get("findings", [])
    )
    return ChangeView(
        project_id=value["project_id"], change_id=value["change_id"],
        locator=value["locator"], revision=value["revision"],
        scope_hash=value["scope_hash"], state=value["state"],
        base_git_sha=value["base_git_sha"], schema_hash=value["schema_hash"],
        documents=documents, findings=findings,
        approval_id=value.get("approval_id"), next_action=value.get("next_action", "validate"),
    )


def _render_export(
    change: ChangeView, format_name: str,
    exchange: WorkbookExchange | None = None,
) -> ExportPayload:
    documents = sorted(change.documents, key=lambda item: item.path)
    metadata = {
        "project_id": change.project_id, "change_id": change.change_id,
        "revision": change.revision, "scope_hash": change.scope_hash,
    }
    if format_name == "csv":
        data = (exchange or WorkbookExchange()).render_csv(change, excel_utf8=True)
        return ExportPayload(f"{change.change_id}_wbs.csv", "text/csv; charset=utf-8", data, metadata)
    markdown = "\n\n".join(
        f"# {item.path}\n\n{item.source.rstrip()}" for item in documents
    ) + "\n"
    if format_name == "markdown":
        return ExportPayload(f"{change.change_id}_planning.md", "text/markdown; charset=utf-8", markdown.encode(), metadata)
    html_body = "".join(
        f"<article class=artifact><header><p class=path>{html.escape(item.path)}</p></header>{_markdown_to_safe_html(item.source)}</article>"
        for item in documents
    )
    page = ("<!doctype html><meta charset=utf-8><title>Planning export</title>"
            "<style>body{font:15px/1.6 system-ui;max-width:900px;margin:auto;padding:32px;color:#172033}"
            ".artifact{break-after:page;margin:0 0 48px}.path{color:#667085;font:12px ui-monospace}"
            "pre{white-space:pre-wrap;border:1px solid #ccd;padding:16px;overflow-wrap:anywhere}"
            "h1,h2,h3{line-height:1.25} @media print{body{padding:0}.artifact:last-child{break-after:auto}}</style>" + html_body).encode()
    if format_name == "html":
        return ExportPayload(f"{change.change_id}_planning.html", "text/html; charset=utf-8", page, metadata)
    if format_name == "zip":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("planning.md", markdown)
            archive.writestr("print.html", page)
            archive.writestr("manifest.json", json.dumps(metadata, sort_keys=True, indent=2))
            for item in documents:
                archive.writestr("documents/" + item.path.replace("..", "_"), item.source)
        return ExportPayload(f"{change.change_id}_planning.zip", "application/zip", output.getvalue(), metadata)
    raise PmError("invalid_request", "format must be xlsx, csv, markdown, html, or zip")


def _operation(registry: Any, operation_id: str, *, project_id: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f-]{16,64}", operation_id):
        raise PmError("operation_not_found", "unknown operation", status=404)
    row = registry._connect().execute(
        "SELECT operation_id, kind, state, project_id, change_id, expected_revision, result_json, error_code FROM pm_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise PmError("operation_not_found", "unknown operation", status=404)
    if project_id is not None and str(row["project_id"]) != project_id:
        raise PmError("operation_not_found", "unknown operation", status=404)
    return {
        "operation_id": row["operation_id"], "kind": row["kind"],
        "state": row["state"], "project_id": row["project_id"],
        "change_id": row["change_id"], "expected_revision": row["expected_revision"],
        "result": json.loads(row["result_json"] or "{}"), "error_code": row["error_code"],
    }


_PLANNING_ID_PLACEHOLDER = re.compile(
    r"@@SYMPHONY_NEW_(REQ|SC|SB|API|DEC|T|G)_([A-Z0-9_]{1,64})@@"
)


def _reserve_planning_ids(
    registry: Any, operation_id: str, proposals: list[dict[str, Any]], *,
    occupied_ids: set[str] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    placeholder_targets: dict[str, set[str]] = {}
    for proposal in proposals:
        document_id = str(proposal.get("document_id") or "")
        for match in _PLANNING_ID_PLACEHOLDER.finditer(
            str(proposal.get("proposal_patch") or "")
        ):
            placeholder_targets.setdefault(match.group(0), set()).add(document_id)
    placeholders = sorted(placeholder_targets)
    if len(placeholders) > 100:
        raise PmError("invalid_proposal", "planning proposal requests too many identities", status=409)
    conn = registry._connect()
    existing = {
        str(row["placeholder"]): str(row["permanent_id"])
        for row in conn.execute(
            "SELECT placeholder,permanent_id FROM pm_planning_reserved_ids WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
    }
    mapping = dict(existing)
    occupied = set(occupied_ids or ()) | set(mapping.values())
    for placeholder in placeholders:
        match = _PLANNING_ID_PLACEHOLDER.fullmatch(placeholder)
        assert match is not None
        prefix = match.group(1)
        if any(
            not _planning_identity_target_allowed(prefix, document_id)
            for document_id in placeholder_targets[placeholder]
        ):
            raise PmError(
                "invalid_proposal",
                "planning identity placeholder targets the wrong document",
                status=409,
                details={"placeholder": placeholder},
            )
        if placeholder in mapping:
            continue
        for _ in range(128):
            permanent = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
            if permanent not in occupied:
                break
        else:
            raise PmError(
                "identity_exhausted", "could not reserve planning identity", status=503
            )
        try:
            conn.execute(
                "INSERT INTO pm_planning_reserved_ids(operation_id,placeholder,kind,permanent_id,state) VALUES (?,?,?,?,'RESERVED')",
                (operation_id, placeholder, prefix, permanent),
            )
        except sqlite3.IntegrityError as exc:
            raise PmError("identity_collision", "could not reserve planning identity", status=409) from exc
        mapping[placeholder] = permanent
        occupied.add(permanent)
    rewritten: list[dict[str, Any]] = []
    for proposal in proposals:
        patch = str(proposal.get("proposal_patch") or "")
        for placeholder, permanent in mapping.items():
            patch = patch.replace(placeholder, permanent)
        rewritten.append({"document_id": str(proposal.get("document_id") or ""), "proposal_patch": patch})
    return mapping, rewritten


def _planning_identity_target_allowed(prefix: str, document_id: str) -> bool:
    if prefix in {"REQ", "SC"}:
        return document_id.startswith("spec:")
    return {
        "SB": "ux",
        "API": "design",
        "DEC": "proposal",
        "T": "tasks",
        "G": "tasks",
    }.get(prefix) == document_id


def _planning_proposal_preflight(
    proposals: list[dict[str, Any]], editable: dict[str, DocumentView]
) -> tuple[str, ...]:
    findings: list[str] = []
    placeholders: dict[str, str] = {}
    updated_sources: dict[str, str] = {}
    for proposal in proposals:
        document_id = str(proposal.get("document_id") or "")
        document = editable.get(document_id)
        if document is None:
            findings.append("invalid_planning_scope")
            continue
        patch = str(proposal.get("proposal_patch") or "")
        for match in _PLANNING_ID_PLACEHOLDER.finditer(patch):
            placeholder = match.group(0)
            if placeholder not in placeholders:
                placeholders[placeholder] = (
                    f"{match.group(1)}-{len(placeholders) + 1:08X}"
                )
            patch = patch.replace(placeholder, placeholders[placeholder])
        try:
            updated = _apply_unified_diff(document.source, patch)
        except PmError as exc:
            findings.append(exc.code)
            continue
        updated_sources[document_id] = updated
        if document_id == "tasks":
            parsed = parse_wbs(updated)
            findings.extend(
                item.code for item in parsed.findings
                if item.severity == "blocking"
            )
            before_ids = {item.task_id for item in parse_wbs(document.source).tasks}
            after_ids = {item.task_id for item in parsed.tasks}
            allowed = {
                value for value in placeholders.values()
                if value.startswith(("T-", "G-"))
            }
            if (after_ids - before_ids) - allowed:
                findings.append("work_item_id_replacement")
    documents_after = tuple(
        DocumentView(
            document.document_id, document.path,
            updated_sources.get(document.document_id, document.source),
            document.content_hash, document.state, document.editable,
            document.unsupported_reason,
        )
        for document in editable.values()
    )
    before_semantic = build_semantic_index(tuple(editable.values()))
    after_semantic = build_semantic_index(documents_after)
    before_semantic_ids = {item.semantic_id for item in before_semantic.items}
    after_semantic_ids = {item.semantic_id for item in after_semantic.items}
    allowed_semantic = {
        value for value in placeholders.values()
        if value.startswith(("REQ-", "SC-", "SB-", "API-", "DEC-"))
    }
    if (after_semantic_ids - before_semantic_ids) - allowed_semantic:
        findings.append("semantic_id_replacement")
    findings.extend(
        item.code for item in after_semantic.findings
        if item.severity == "blocking"
        and item.code in {
            "duplicate_semantic_id", "retired_semantic_id_reused",
            "semantic_id_kind_mismatch", "semantic_kind_wrong_document",
        }
    )
    return tuple(sorted(set(findings)))


def _assert_planning_reservations(
    registry: Any, operation_id: str, reserved_ids: Any
) -> None:
    if not isinstance(reserved_ids, dict):
        raise PmError(
            "invalid_proposal", "planning proposal identity reservation is invalid", status=409
        )
    expected = {str(key): str(value) for key, value in reserved_ids.items()}
    if not expected:
        return
    rows = registry._connect().execute(
        "SELECT placeholder, permanent_id, state FROM pm_planning_reserved_ids "
        "WHERE operation_id=?",
        (operation_id,),
    ).fetchall()
    actual = {
        str(row["placeholder"]): (str(row["permanent_id"]), str(row["state"]))
        for row in rows
    }
    if set(actual) != set(expected) or any(
        actual[placeholder] != (permanent_id, "RESERVED")
        for placeholder, permanent_id in expected.items()
    ):
        raise PmError(
            "operation_not_ready",
            "planning proposal identities are no longer reserved",
            status=409,
        )


def _markdown_to_safe_html(source: str) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    fenced = False
    code: list[str] = []
    def flush_paragraph() -> None:
        if paragraph:
            output.append("<p>" + " ".join(html.escape(item.strip()) for item in paragraph) + "</p>")
            paragraph.clear()
    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = None
    for raw in source.splitlines():
        if raw.startswith("```"):
            flush_paragraph()
            close_list()
            if fenced:
                output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code.clear()
            fenced = not fenced
            continue
        if fenced:
            code.append(raw)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", raw)
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", raw)
        ordered = re.match(r"^\s*\d+[.)]\s+(.+)$", raw)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        elif bullet or ordered:
            flush_paragraph()
            desired = "ul" if bullet else "ol"
            if list_kind != desired:
                close_list()
                list_kind = desired
                output.append(f"<{desired}>")
            item = bullet if bullet is not None else ordered
            assert item is not None
            output.append("<li>" + html.escape(item.group(1)) + "</li>")
        elif raw.strip():
            close_list()
            paragraph.append(raw)
        else:
            flush_paragraph()
            close_list()
    if fenced:
        output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_paragraph()
    close_list()
    return "".join(output)


def _apply_unified_diff(source: str, patch: str) -> str:
    original = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in source else "\n"

    def source_text(value: str) -> str:
        if value.endswith("\r\n"):
            return value[:-2]
        if value.endswith(("\n", "\r")):
            return value[:-1]
        return value

    def without_newline(value: str) -> str:
        if value.endswith("\r\n"):
            return value[:-2]
        if value.endswith(("\n", "\r")):
            return value[:-1]
        return value

    output: list[str] = []

    def append_output(value: str) -> None:
        if output and not output[-1].endswith(("\n", "\r")):
            raise PmError(
                "invalid_proposal",
                "proposal joins content after an unterminated line",
                status=409,
            )
        output.append(value)

    cursor = 0
    lines = [line[:-1] if line.endswith("\r") else line for line in patch.split("\n")]
    index = 0
    while index < len(lines) and not lines[index]:
        index += 1
    if index < len(lines) and lines[index].startswith(("---", "+++")):
        if not re.fullmatch(r"--- \S.*", lines[index]):
            raise PmError(
                "invalid_proposal",
                "proposal unified diff headers are malformed",
                status=409,
            )
        index += 1
        if index >= len(lines) or not re.fullmatch(r"\+\+\+ \S.*", lines[index]):
            raise PmError(
                "invalid_proposal",
                "proposal unified diff headers are incomplete",
                status=409,
            )
        index += 1
    saw_hunk = False
    declared_new_eof_index: int | None = None
    while index < len(lines):
        line = lines[index]
        if not saw_hunk and not line:
            index += 1
            continue
        match = re.fullmatch(
            r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?",
            line,
        )
        if match is None:
            raise PmError(
                "invalid_proposal",
                "proposal contains content outside a unified diff hunk",
                status=409,
            )
        saw_hunk = True
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_start = int(match.group(3))
        new_count = int(match.group(4) or 1)
        if (old_count > 0 and old_start == 0) or (
            new_count > 0 and new_start == 0
        ):
            raise PmError(
                "invalid_proposal",
                "proposal hunk uses an invalid zero line position",
                status=409,
            )
        start = old_start if old_count == 0 else old_start - 1
        if start < cursor or start > len(original):
            raise PmError("proposal_conflict", "proposal hunk is outside the document", status=409)
        for unchanged in original[cursor:start]:
            append_output(unchanged)
        cursor = start
        expected_new_start = len(output) if new_count == 0 else len(output) + 1
        if new_start != expected_new_start:
            raise PmError(
                "invalid_proposal",
                "proposal hunk new position is inconsistent",
                status=409,
            )
        index += 1
        observed_old = 0
        observed_new = 0
        saw_content = False
        last_content_marker = ""
        last_had_no_newline = False
        last_old_line: str | None = None
        last_old_index: int | None = None
        pending_old_eof_marker = False
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line == "\\ No newline at end of file":
                if not saw_content or last_had_no_newline:
                    raise PmError(
                        "invalid_proposal",
                        "proposal has a misplaced no-newline marker",
                        status=409,
                    )
                if last_content_marker in {" ", "-"} and (
                    last_old_index != len(original) - 1
                    or last_old_line is None
                    or last_old_line.endswith(("\n", "\r"))
                ):
                    raise PmError(
                        "invalid_proposal",
                        "proposal no-newline marker does not identify old EOF",
                        status=409,
                    )
                if last_content_marker in {" ", "+"}:
                    output[-1] = without_newline(output[-1])
                    if declared_new_eof_index is None:
                        declared_new_eof_index = len(output) - 1
                last_had_no_newline = True
                pending_old_eof_marker = False
                index += 1
                continue
            if not line and index == len(lines) - 1:
                if pending_old_eof_marker:
                    raise PmError(
                        "invalid_proposal",
                        "proposal omits an old EOF no-newline marker",
                        status=409,
                    )
                index += 1
                break
            if pending_old_eof_marker:
                raise PmError(
                    "invalid_proposal",
                    "proposal omits an old EOF no-newline marker",
                    status=409,
                )
            marker, value = (line[:1], line[1:]) if line else ("", "")
            if marker in {" ", "-"}:
                if cursor >= len(original) or source_text(original[cursor]) != value:
                    raise PmError("proposal_conflict", "proposal no longer matches this revision", status=409)
                last_old_line = original[cursor]
                last_old_index = cursor
                pending_old_eof_marker = not original[cursor].endswith(("\n", "\r"))
                if marker == " ":
                    append_output(original[cursor])
                cursor += 1
                observed_old += 1
                if marker == " ":
                    observed_new += 1
            elif marker == "+":
                append_output(value + newline)
                observed_new += 1
            else:
                raise PmError(
                    "invalid_proposal",
                    "proposal contains an unsupported unified diff line",
                    status=409,
                )
            saw_content = True
            last_content_marker = marker
            last_had_no_newline = False
            index += 1
        if pending_old_eof_marker:
            raise PmError(
                "invalid_proposal",
                "proposal omits an old EOF no-newline marker",
                status=409,
            )
        if observed_old != old_count or observed_new != new_count:
            raise PmError(
                "invalid_proposal",
                "proposal hunk line counts are incomplete",
                status=409,
            )
        if (
            declared_new_eof_index is not None
            and declared_new_eof_index != len(output) - 1
        ):
            raise PmError(
                "invalid_proposal",
                "proposal contains content after new EOF",
                status=409,
            )
    if not saw_hunk:
        raise PmError("invalid_proposal", "proposal does not contain a unified diff")
    for unchanged in original[cursor:]:
        append_output(unchanged)
    if (
        declared_new_eof_index is not None
        and declared_new_eof_index != len(output) - 1
    ):
        raise PmError(
            "invalid_proposal",
            "proposal contains content after new EOF",
            status=409,
        )
    result = "".join(output)
    return result


def _successful_terminal_states(cfg: Any) -> tuple[str, ...]:
    return tuple(
        state for state in cfg.tracker.terminal_states
        if state.strip().casefold() == "done"
    )


def _ticket_code_evidence(
    registry: Any, project_id: str, ticket_identifier: str,
    task_revision: str, base_git_sha: str,
    successful_terminal_states: tuple[str, ...] = ("Done",),
) -> dict[str, Any]:
    states = frozenset(state.strip().casefold() for state in successful_terminal_states)
    runs = [
        item for item in registry.recent_runs(ticket_identifier, limit=20)
        if successful_run_provenance(item, states)
    ]
    if not runs:
        return {"run_id": None, "head_commit": None, "base_commit": base_git_sha, "code_files": [], "receipts": []}
    run = runs[0]
    files: list[dict[str, str]] = []
    if re.fullmatch(r"[0-9a-f]{40}", base_git_sha or "") and re.fullmatch(r"[0-9a-f]{40}", run.commit_sha or ""):
        completed = subprocess.run(
            ["git", "-C", str(run.workspace_path), "diff", "--name-status", "--no-renames", f"{base_git_sha}..{run.commit_sha}"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            for line in completed.stdout.splitlines()[:500]:
                status, _, path = line.partition("\t")
                if path and ".." not in Path(path).parts:
                    files.append({"status": status, "path": path})
    # Registry receipt lookup is scoped by project at the call site; collect the
    # run-bound records directly here to preserve evidence even before acceptance.
    rows = registry._connect().execute(
        "SELECT receipt_id, procedure_id, result, artifact_refs_json FROM pm_verification_records WHERE project_id = ? AND ticket_identifier = ? AND task_revision = ? AND tested_commit = ? ORDER BY completed_at",
        (project_id, ticket_identifier, task_revision, run.commit_sha),
    ).fetchall()
    receipts = [{"receipt_id": row["receipt_id"], "procedure_id": row["procedure_id"], "result": row["result"], "artifact_refs": json.loads(row["artifact_refs_json"] or "[]")} for row in rows]
    return {"run_id": run.run_id, "head_commit": run.commit_sha, "base_commit": base_git_sha, "code_files": files, "receipts": receipts}


def _import_recovery_matches(
    repository: PmDocumentRepository,
    ctx: PmRequestContext,
    prepared: dict[str, Any],
    *,
    owner: str,
) -> bool:
    expected_revision = prepared.get("expected_post_revision")
    expected_manifest = prepared.get("expected_manifest")
    if not isinstance(expected_revision, str) or not isinstance(expected_manifest, dict):
        return False
    current = repository.managed_manifest(ctx, owner=owner)
    return (
        current["revision"] == expected_revision
        and current["files"] == expected_manifest
    )


def _openspec_readiness(
    adapter: OpenSpecAdapter, root: Path, locator: str
) -> ValidationReport:
    report = adapter.validate(root, locator)
    findings = list(report.findings)
    status = adapter.status(root, locator)
    instructions = adapter.apply_instructions(root, locator)
    tasks_path = root / "openspec" / "changes" / locator / "tasks.md"
    parsed = parse_wbs(tasks_path.read_text(encoding="utf-8"))
    executable = sum(task.executable for task in parsed.tasks)
    if not status.planning_complete:
        findings.append(PmFinding("openspec_incomplete", "OpenSpec planning artifacts are incomplete", severity="blocking"))
    if instructions.total != executable or len(instructions.tasks) != executable:
        findings.append(PmFinding("openspec_task_mismatch", "OpenSpec task count differs from executable PM leaves", severity="blocking"))
    return ValidationReport(report.valid and not any(item.severity == "blocking" for item in findings), tuple(findings), "openspec+pm", "1.14.2")
