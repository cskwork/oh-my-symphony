"""Routes: board + issues."""

from __future__ import annotations

import asyncio
import re
from functools import partial
from typing import Any

from aiohttp import web

from .. import installed_components, pm_installed
from ..errors import SymphonyError
from ..issue import Issue, registration_order_key
from ..logging import get_logger
from ..orchestrator import Orchestrator
from ..orchestrator.run_registry import clamp_run_history_limit
from ..orchestrator.scheduler import (
    MAX_DEPENDENCY_EDGES,
    MAX_DEPENDENCY_NODES,
    RequestGroupKey,
    group_by_request,
)
from ..skills import normalize_skill_names
from ..trackers.file import FileBoardTracker, parse_ticket_file
from ..trackers.validate import validate_ticket_dependencies
from ..workflow import (
    selectable_agent_kinds,
    ServiceConfig,
)
from ..workflow.mutate import WorkflowMutationError
from .context import _Ctx
from .payloads import (
    _columns_payload,
    _issue_card,
    _json_safe,
    _live_by_identifier,
    _request_group_schedule_payload,
)
from .security import (
    _RUN_ID_RE,
    _json_error,
    _read_json,
    _request_is_loopback,
    _wrap,
)
from .validation import (
    _INLINE_ARTIFACT_TYPES,
    _artifact_json,
    _ascii_filename,
    _check_agent_kind,
    _check_blocked_by,
    _check_description,
    _check_identifier,
    _check_labels,
    _check_priority,
    _check_request,
    _check_request_schedule_key,
    _check_state,
    _check_title,
    _valid_states,
)

log = get_logger()


# ---------------------------------------------------------------------------
# routes: board + issues
# ---------------------------------------------------------------------------


def _register_issue_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    async def handle_runs(request: web.Request) -> web.Response:
        if not _request_is_loopback(request):
            return _json_error(
                403,
                "run_diagnostics_local_only",
                "run diagnostics are available only from the local machine",
            )
        raw_limit = request.query.get("limit", "50")
        try:
            limit = clamp_run_history_limit(int(raw_limit))
        except (TypeError, ValueError):
            limit = clamp_run_history_limit(50)
        issue_id = request.query.get("issue") or None
        query = (request.query.get("query") or "").strip()[:300] or None
        status = (request.query.get("status") or "").strip()[:100] or None
        agent = (request.query.get("agent") or "").strip()[:100] or None
        runs, registry_error = await orchestrator.recent_runs(
            issue_id=issue_id,
            limit=limit,
            query=query,
            status=status,
            agent=agent,
        )
        payload: dict[str, Any] = {"runs": runs, "count": len(runs)}
        if registry_error:
            payload["registry_error"] = registry_error
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def handle_run_detail(request: web.Request) -> web.Response:
        if not _request_is_loopback(request):
            return _json_error(
                403,
                "run_diagnostics_local_only",
                "run diagnostics are available only from the local machine",
            )
        run_id = request.match_info["run_id"].strip()
        if not _RUN_ID_RE.fullmatch(run_id):
            return _json_error(
                400, "invalid_run_id", "run_id must be 32 lowercase hex characters"
            )
        detail, registry_error = await orchestrator.run_detail(run_id)
        if registry_error:
            return _json_error(503, "run_registry_unavailable", registry_error)
        if detail is None:
            return _json_error(404, "run_not_found", f"unknown run {run_id}")
        return web.json_response(detail, headers={"Cache-Control": "no-store"})

    async def handle_run_diagnostic(request: web.Request) -> web.Response:
        if not _request_is_loopback(request):
            return _json_error(
                403,
                "run_diagnostics_local_only",
                "run diagnostics are available only from the local machine",
            )
        run_id = request.match_info["run_id"].strip()
        if not _RUN_ID_RE.fullmatch(run_id):
            return _json_error(
                400, "invalid_run_id", "run_id must be 32 lowercase hex characters"
            )
        diagnostic, registry_error = await orchestrator.run_diagnostic(run_id)
        if registry_error:
            return _json_error(503, "run_registry_unavailable", registry_error)
        if diagnostic is None:
            return _json_error(404, "run_not_found", f"unknown run {run_id}")
        filename = f"symphony-run-{run_id}-diagnostic.json"
        return web.json_response(
            diagnostic,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def handle_board(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        issues: list[dict[str, Any]] = []
        read_only = cfg.tracker.kind != "file"
        if not read_only:
            tracker = FileBoardTracker(cfg.tracker)
            all_states = list(_valid_states(cfg).values())
            fetched = await asyncio.to_thread(
                tracker.fetch_issues_by_states, all_states
            )
            issues = [
                _issue_card(i, attention=orchestrator.issue_attention(i))
                for i in sorted(fetched, key=registration_order_key)
            ]
        return web.json_response(
            {
                "board": {
                    "name": ctx.workflow_dir().name,
                    "tracker_kind": cfg.tracker.kind,
                    "read_only": read_only,
                    "default_agent_kind": cfg.agent.kind,
                    "agent_kinds": sorted(selectable_agent_kinds(cfg.agent.profiles)),
                    # The optional PM Workspace distribution backs the Artifacts page.
                    "pm_available": pm_installed(),
                    # Every optional distribution (`packages/*`), so the UI can
                    # hide entry points whose component is not installed.
                    "components": installed_components(),
                },
                "columns": _columns_payload(cfg),
                "issues": issues,
                "live": _live_by_identifier(orchestrator),
            }
        )

    async def _fetch_all_file_issues(cfg: ServiceConfig) -> list[Issue]:
        tracker = FileBoardTracker(cfg.tracker)
        all_states = list(_valid_states(cfg).values())
        return await asyncio.to_thread(tracker.fetch_issues_by_states, all_states)

    async def handle_requests(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        schedule = orchestrator.schedule_snapshot()
        if cfg.tracker.kind != "file":
            return web.json_response(
                {
                    "available": False,
                    "reason": "unsupported_tracker",
                    "tracker_kind": cfg.tracker.kind,
                    "requests": [],
                },
                headers={"Cache-Control": "no-store"},
            )
        issues = await _fetch_all_file_issues(cfg)
        edge_count = sum(len(issue.blocked_by) for issue in issues)
        if len(issues) > MAX_DEPENDENCY_NODES or edge_count > MAX_DEPENDENCY_EDGES:
            return _json_error(
                413,
                "schedule_graph_too_large",
                "request schedule graph exceeds the safe read limit",
            )
        groups = group_by_request(issues)
        decisions = {
            row.get("identifier"): row
            for row in schedule.get("entries", [])
            if isinstance(row, dict) and isinstance(row.get("identifier"), str)
        }
        terminal_states = {
            state.strip().lower() for state in cfg.tracker.terminal_states
        }
        requests_payload: list[dict[str, Any]] = []
        catalog_drifted = False
        for key, members in groups.items():
            counts = {
                "running": 0,
                "ready": 0,
                "waiting": 0,
                "retrying": 0,
                "successful": 0,
                "needs_action": 0,
            }
            for issue in members:
                decision = decisions.get(issue.identifier)
                current_updated_at = (
                    issue.updated_at.isoformat() if issue.updated_at else None
                )
                member_drifted = bool(
                    (
                        decision is not None
                        and (
                            (
                                decision.get("evaluated_state") is not None
                                and decision.get("evaluated_state") != issue.state
                            )
                            or (
                                decision.get("evaluated_updated_at") is not None
                                and decision.get("evaluated_updated_at")
                                != current_updated_at
                            )
                            or (
                                "evaluated_request" in decision
                                and decision.get("evaluated_request")
                                != ((issue.request or "").strip() or None)
                            )
                        )
                    )
                    or (
                        decision is None
                        and bool(schedule.get("available"))
                        and issue.state.strip().lower() not in terminal_states
                    )
                )
                catalog_drifted = catalog_drifted or member_drifted
                if member_drifted:
                    status = "needs_action"
                elif decision is not None:
                    status = str(decision.get("status") or "waiting")
                elif issue.state.strip().lower() in terminal_states:
                    status = (
                        "successful"
                        if orchestrator.dependency_state_resolved(issue.state)
                        else "needs_action"
                    )
                else:
                    status = "waiting"
                counts[status if status in counts else "waiting"] += 1
            requests_payload.append(
                {
                    "kind": key.kind,
                    "id": key.value,
                    "node_count": len(members),
                    "counts": counts,
                    # The complete prerequisite closure is built only by the
                    # detail endpoint; do not substitute the global DAG rank.
                    "longest_unresolved_chain_nodes": None,
                }
            )
        requests_payload.sort(
            key=lambda row: (
                row["kind"] != "request",
                -sum(
                    int(row["counts"].get(status, 0))
                    for status in (
                        "running",
                        "ready",
                        "waiting",
                        "retrying",
                        "needs_action",
                    )
                ),
                str(row["id"]).casefold(),
                str(row["id"]),
            )
        )
        return web.json_response(
            {
                "available": bool(schedule.get("available")),
                "reason": schedule.get("reason"),
                "tracker_kind": cfg.tracker.kind,
                "generated_at": schedule.get("generated_at"),
                "stale": bool(schedule.get("stale")) or catalog_drifted,
                "decision_drifted": catalog_drifted,
                "policy": schedule.get("policy", cfg.agent.scheduling_policy),
                "requests": requests_payload,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def handle_request_schedule(request: web.Request) -> web.Response:
        cfg = ctx.config()
        if cfg.tracker.kind != "file":
            return web.json_response(
                {
                    "available": False,
                    "reason": "unsupported_tracker",
                    "tracker_kind": cfg.tracker.kind,
                },
                headers={"Cache-Control": "no-store"},
            )
        kind = (
            request.query.get("kind", request.match_info.get("kind", "request"))
            .strip()
            .lower()
        )
        if kind not in {"request", "ticket"}:
            return _json_error(
                400, "invalid_request_kind", "kind must be request or ticket"
            )
        raw_identifier = request.query.get("id", request.match_info.get("identifier"))
        identifier = (
            _check_request_schedule_key(raw_identifier)
            if "id" in request.query
            else _check_identifier(str(raw_identifier or ""))
        )
        issues = await _fetch_all_file_issues(cfg)
        edge_count = sum(len(issue.blocked_by) for issue in issues)
        if len(issues) > MAX_DEPENDENCY_NODES or edge_count > MAX_DEPENDENCY_EDGES:
            return _json_error(
                413,
                "schedule_graph_too_large",
                "request schedule graph exceeds the safe read limit",
            )
        groups = group_by_request(issues)
        key = RequestGroupKey("request" if kind == "request" else "ticket", identifier)
        members = groups.get(key)
        if members is None:
            return _json_error(
                404,
                "request_not_found",
                f"unknown {kind} schedule {identifier}",
            )
        payload = _request_group_schedule_payload(
            key=key,
            members=members,
            all_issues=issues,
            schedule=orchestrator.schedule_snapshot(),
            orchestrator=orchestrator,
            cfg=cfg,
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def handle_issue_create(request: web.Request) -> web.Response:
        body = await _read_json(request)
        cfg = ctx.config()
        tracker = ctx.file_tracker()
        title = _check_title(body.get("title"))
        state = (
            _check_state(cfg, body.get("state"))
            if body.get("state")
            else (cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo")
        )
        blocked_by = _check_blocked_by(body.get("blocked_by"))
        fields = {
            "title": title,
            "state": state,
            "priority": _check_priority(body.get("priority")),
            "labels": _check_labels(body.get("labels")),
            "description": _check_description(body.get("description")),
            "agent_kind": _check_agent_kind(body.get("agent_kind"), cfg) or None,
            "skills": list(normalize_skill_names(body.get("skills") or [])),
            "blocked_by": blocked_by or None,
            "request": _check_request(body.get("request")) or None,
        }

        # F-15: validate and write under the same board lock so concurrent
        # creates cannot jointly introduce a cycle or race the allocator.
        def _validate(issues: list[Issue], resolved: str) -> None:
            validate_ticket_dependencies(
                issues,
                identifier=resolved,
                blocked_by=blocked_by,
                new_ticket=True,
            )

        raw_identifier = body.get("identifier")
        prefix = "TASK"
        identifier_arg: str | None = None
        if raw_identifier:
            if not isinstance(raw_identifier, str):
                raise WorkflowMutationError("identifier must be a string")
            identifier_arg = _check_identifier(raw_identifier)
        else:
            prefix_raw = body.get("prefix")
            prefix = (
                prefix_raw.strip().upper()
                if isinstance(prefix_raw, str) and prefix_raw.strip()
                else "TASK"
            )
            if not re.match(r"^[A-Za-z][A-Za-z0-9]{0,15}$", prefix):
                raise WorkflowMutationError("prefix must be 1-16 alphanumeric chars")
        identifier, _ = await asyncio.to_thread(
            partial(
                tracker.create_validated,
                identifier=identifier_arg,
                prefix=prefix,
                validate=_validate,
                **fields,
            )
        )
        await asyncio.to_thread(
            ctx.stats().record_transition,
            issue=identifier,
            from_state="",
            to_state=state.lower(),
        )
        orchestrator.request_refresh()
        return web.json_response({"identifier": identifier, "state": state}, status=201)

    async def handle_issue_detail(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        tracker = ctx.file_tracker()
        path = await asyncio.to_thread(tracker.find_path, identifier)
        if path is None:
            return _json_error(404, "issue_not_found", f"unknown issue {identifier}")
        front, body_text = await asyncio.to_thread(parse_ticket_file, path)
        issue = await asyncio.to_thread(tracker.fetch_issue_full_by_id, identifier)
        card = (
            _issue_card(issue, attention=orchestrator.issue_attention(issue))
            if issue
            else {"identifier": identifier}
        )
        live = _live_by_identifier(orchestrator).get(identifier)
        scheduling: dict[str, Any] = {"available": False, "stale": False, "node": None}
        budgets: dict[str, Any] = {"available": False, "items": []}
        if issue is not None:
            try:
                issues = await _fetch_all_file_issues(ctx.config())
                if len(issues) <= MAX_DEPENDENCY_NODES and sum(len(i.blocked_by) for i in issues) <= MAX_DEPENDENCY_EDGES:
                    projection = _request_group_schedule_payload(
                        key=RequestGroupKey("ticket", issue.identifier),
                        members=[issue], all_issues=issues,
                        schedule=orchestrator.schedule_snapshot(),
                        orchestrator=orchestrator, cfg=ctx.config(),
                    )
                    scheduling = {
                        key: projection[key] for key in ("available", "stale", "generated_at")
                    }
                    scheduling["node"] = next(
                        (node for node in projection["nodes"] if node["identifier"] == identifier), None
                    )
            except Exception as exc:
                log.warning("issue_schedule_unavailable", identifier=identifier, error=str(exc))
            budget_reader = getattr(orchestrator, "issue_budget_snapshot", None)
            if callable(budget_reader):
                try:
                    budgets = await orchestrator.issue_budget_snapshot(issue)
                except Exception as exc:
                    log.warning("issue_budgets_unavailable", identifier=identifier, error=str(exc))
        store = ctx.artifacts()
        artifacts = (
            [
                _artifact_json(identifier, record)
                for record in await asyncio.to_thread(store.list_for, identifier)
            ]
            if store is not None
            else []
        )
        return web.json_response(
            {
                **card,
                "description": body_text,
                "frontmatter": _json_safe(front),
                "live": live,
                "scheduling": scheduling,
                "budgets": budgets,
                "artifacts": artifacts,
            }
        )

    async def handle_issue_artifacts(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        store = ctx.artifacts()
        if store is None:
            return web.json_response({"artifacts": [], "enabled": False})
        records = await asyncio.to_thread(store.list_for, identifier)
        return web.json_response(
            {
                "artifacts": [_artifact_json(identifier, r) for r in records],
                "enabled": True,
            }
        )

    async def handle_issue_artifact_file(request: web.Request) -> web.StreamResponse:
        identifier = _check_identifier(request.match_info["identifier"])
        name = request.match_info["name"]
        store = ctx.artifacts()
        if store is None:
            return _json_error(404, "artifacts_disabled", "artifacts are disabled")
        # One index read for both the record and the path: with a corrupt
        # index each read re-hashes the whole ticket directory.
        record = await asyncio.to_thread(store.record_for, identifier, name)
        path = (
            await asyncio.to_thread(store.resolve_file, identifier, name)
            if record is not None
            else None
        )
        if path is None or record is None:
            return _json_error(
                404, "artifact_not_found", f"unknown artifact {name} on {identifier}"
            )
        # Worker-authored bytes served from the board's own origin: anything
        # the browser could execute in this document's context (HTML, SVG,
        # scripts) must download instead of render, and nosniff stops a
        # text/plain payload being re-typed as HTML.
        inline = record.content_type in _INLINE_ARTIFACT_TYPES
        disposition = "inline" if inline else "attachment"
        return web.FileResponse(
            path,
            headers={
                "Content-Type": record.content_type,
                "Content-Disposition": (
                    f'{disposition}; filename="{_ascii_filename(name)}"'
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                # Backstop for the allowlist above: even if a renderable
                # type is ever added by mistake, these bytes are worker
                # -authored and must not run script, load subresources, or
                # frame anything on the board's origin.
                "Content-Security-Policy": "default-src 'none'; sandbox allow-downloads",
                "X-Frame-Options": "DENY",
            },
        )

    async def handle_issue_patch(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        body = await _read_json(request)
        cfg = ctx.config()
        tracker = ctx.file_tracker()
        current = await asyncio.to_thread(tracker.fetch_issue_full_by_id, identifier)
        if current is None:
            return _json_error(404, "issue_not_found", f"unknown issue {identifier}")

        fields: dict[str, Any] = {}
        if "title" in body:
            fields["title"] = _check_title(body.get("title"))
        if "description" in body:
            fields["description"] = _check_description(body.get("description"))
        if "priority" in body:
            priority = _check_priority(body.get("priority"))
            if priority is None:
                fields["clear_priority"] = True
            else:
                fields["priority"] = priority
        if "labels" in body:
            fields["labels"] = _check_labels(body.get("labels"))
        if "skills" in body:
            raw_skills = body.get("skills")
            if raw_skills is not None and not isinstance(raw_skills, list):
                raise WorkflowMutationError("skills must be a list of names")
            fields["skills"] = [
                str(s) for s in (raw_skills or []) if isinstance(s, str)
            ]
        if "agent_kind" in body:
            fields["agent_kind"] = _check_agent_kind(body.get("agent_kind"), cfg)
        if "request" in body:
            fields["request"] = _check_request(body.get("request"))
        if "blocked_by" in body:
            blocked_by = _check_blocked_by(body.get("blocked_by"))
            await asyncio.to_thread(
                lambda: validate_ticket_dependencies(
                    tracker.scan_all(),
                    identifier=identifier,
                    blocked_by=blocked_by,
                    new_ticket=False,
                )
            )
            fields["blocked_by"] = blocked_by
        new_state: str | None = None
        if "state" in body:
            new_state = _check_state(cfg, body.get("state"))
            fields["state"] = new_state

        if not fields:
            return _json_error(400, "empty_patch", "no editable fields in body")
        if (
            new_state is not None
            and new_state.lower() != current.state.lower()
            and orchestrator.find_running_issue_id(identifier) is not None
        ):
            return _json_error(
                409,
                "state_in_use",
                f"{identifier} has a running worker; pause or wait before changing state",
            )
        updated_fields = sorted(fields)
        confirming = (
            current.state.strip().lower() == "human review"
            and new_state is not None
            and new_state.strip().lower() == "done"
        )
        if confirming:
            try:
                await orchestrator.confirm_done(identifier)
            except FileNotFoundError:
                return _json_error(404, "issue_not_found", f"unknown issue {identifier}")
            except ValueError as exc:
                return _json_error(409, "not_confirmable", str(exc))
            fields.pop("state")
        if fields:
            await asyncio.to_thread(tracker.update_fields, identifier, **fields)
        if not confirming and new_state is not None and new_state.lower() != current.state.lower():
            await asyncio.to_thread(
                ctx.stats().record_transition,
                issue=identifier,
                from_state=current.state.lower(),
                to_state=new_state.lower(),
            )
        orchestrator.request_refresh()
        return web.json_response({"identifier": identifier, "updated": updated_fields})

    async def handle_issue_recover_blocked(request: web.Request) -> web.Response:
        cfg = ctx.config()
        identifier = _check_identifier(request.match_info["identifier"])
        body = await _read_json(request)
        raw_target = body.get(
            "fix_state", body.get("rca_state", body.get("target_state"))
        )
        if raw_target is not None and not isinstance(raw_target, str):
            raise WorkflowMutationError("fix_state must be a string")
        agent_kind = (
            _check_agent_kind(body.get("agent_kind"), cfg) if "agent_kind" in body else None
        )
        changed, message, details = await orchestrator.recover_blocked_issue(
            identifier,
            target_state=raw_target,
            agent_kind=agent_kind,
        )
        if not changed:
            status = 404 if message.startswith("unknown issue") else 409
            return _json_error(status, "blocked_recovery_rejected", message)
        return web.json_response(
            {
                "identifier": identifier,
                "fix_created": True,
                # Deprecated alias retained for API compatibility.
                "rca_created": True,
                "message": message,
                **details,
            }
        )

    async def handle_issue_delete(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        tracker = ctx.file_tracker()
        if orchestrator.find_running_issue_id(identifier) is not None:
            return _json_error(
                409,
                "issue_running",
                f"{identifier} has a running worker; pause or wait before deleting",
            )
        try:
            await asyncio.to_thread(tracker.delete, identifier)
        except SymphonyError:
            return _json_error(404, "issue_not_found", f"unknown issue {identifier}")
        orchestrator.request_refresh()
        return web.json_response({"identifier": identifier, "deleted": True})

    async def handle_issue_skip_document(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        changed, message = await orchestrator.skip_document(identifier)
        if not changed:
            status = 404 if message.startswith("unknown issue") else 409
            return _json_error(status, "document_skip_rejected", message)
        return web.json_response(
            {"identifier": identifier, "skipped": True, "message": message}
        )

    app.router.add_get("/api/v1/runs", _wrap(handle_runs))
    app.router.add_get("/api/v1/runs/{run_id}", _wrap(handle_run_detail))
    app.router.add_get("/api/v1/runs/{run_id}/diagnostic", _wrap(handle_run_diagnostic))
    app.router.add_get("/api/v1/board", _wrap(handle_board))
    app.router.add_get("/api/v1/requests", _wrap(handle_requests))
    app.router.add_get(
        "/api/v1/requests/schedule",
        _wrap(handle_request_schedule),
    )
    app.router.add_get(
        "/api/v1/requests/{identifier}/schedule",
        _wrap(handle_request_schedule),
    )
    app.router.add_get(
        "/api/v1/requests/{kind}/{identifier}/schedule",
        _wrap(handle_request_schedule),
    )
    app.router.add_post("/api/v1/issues", _wrap(handle_issue_create))
    app.router.add_get("/api/v1/issues/{identifier}", _wrap(handle_issue_detail))
    app.router.add_patch("/api/v1/issues/{identifier}", _wrap(handle_issue_patch))
    app.router.add_get(
        "/api/v1/issues/{identifier}/artifacts", _wrap(handle_issue_artifacts)
    )
    app.router.add_get(
        "/api/v1/issues/{identifier}/artifacts/{name}",
        _wrap(handle_issue_artifact_file),
    )
    app.router.add_post(
        "/api/v1/issues/{identifier}/recover-blocked",
        _wrap(handle_issue_recover_blocked),
    )
    app.router.add_delete("/api/v1/issues/{identifier}", _wrap(handle_issue_delete))
    app.router.add_post(
        "/api/v1/issues/{identifier}/skip-document", _wrap(handle_issue_skip_document)
    )
    # Deprecated alias — lane renamed Learn -> Document; old scripts keep working.
    app.router.add_post(
        "/api/v1/issues/{identifier}/skip-learn", _wrap(handle_issue_skip_document)
    )
