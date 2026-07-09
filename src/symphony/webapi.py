"""Board web app REST API + static SPA serving.

Registered onto the orchestrator's aiohttp app by `server.build_app`. All
mutations go to the file tracker / WORKFLOW.md through the same modules the
CLI uses (`trackers.file`, `workflow.mutate`), so UI edits and hand edits
stay interchangeable.

Board mutations require `tracker.kind: file`. Read endpoints degrade to
live-run info only for Linear / Jira boards.

Security model: local operator tool. When the server is bound to loopback
(the default), every `/api/` request must carry a loopback Host header —
this blocks DNS-rebinding reads as well as writes. Mutating methods must
additionally send a JSON content type, which forces a CORS preflight on
cross-origin HTML/form attempts. Binding to a non-loopback interface is an
explicit operator opt-in to network exposure and disables the Host check.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from .errors import SymphonyError
from .issue import Issue, registration_order_key
from .logging import get_logger
from .skills import normalize_skill_names
from .stats import StatsStore, stats_store_for
from .trackers.file import FileBoardTracker, parse_ticket_file
from .orchestrator import Orchestrator
from .orchestrator.run_registry import clamp_run_history_limit
from .workflow import SUPPORTED_AGENT_KINDS, ServiceConfig
from .workflow.mutate import (
    StateSpec,
    WorkflowMutationError,
    apply_states_update,
    read_prompt,
    set_branch_policy,
    set_continuous_improvement_settings,
    write_prompt,
)

log = get_logger()

STATIC_DIR = Path(__file__).parent / "web" / "static"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_MAX_TITLE = 300
_MAX_BODY = 128_000
_MAX_LABELS = 20
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
_LOOPBACK_BINDS = {"", "localhost", "127.0.0.1", "::1", "[::1]"}
_CI_EDITABLE_KEYS = {"enabled", "interval_ms", "max_turns", "agent_kind"}


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}}, status=status
    )


# ---------------------------------------------------------------------------
# request plumbing
# ---------------------------------------------------------------------------


def _request_host(request: web.Request) -> str:
    """Host header without the port — bracket-aware for IPv6 literals."""
    raw = (request.host or "").strip().lower()
    if raw.startswith("["):
        return raw.split("]", 1)[0] + "]"
    return raw.rsplit(":", 1)[0]


@web.middleware
async def _api_guard(request: web.Request, handler):
    if request.path.startswith("/api/"):
        bind = str(request.app.get("bind_host") or "127.0.0.1").lower()
        if bind in _LOOPBACK_BINDS and _request_host(request) not in _ALLOWED_HOSTS:
            return _json_error(
                403, "forbidden_host", f"host {request.host!r} not allowed"
            )
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.body_exists
            and request.content_type != "application/json"
        ):
            return _json_error(
                415, "unsupported_media_type", "mutations require application/json"
            )
    return await handler(request)


async def _read_json(request: web.Request) -> dict[str, Any]:
    if not request.body_exists:
        return {}
    try:
        body = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(
            text='{"error":{"code":"invalid_json","message":"body is not JSON"}}',
            content_type="application/json",
        ) from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(
            text='{"error":{"code":"invalid_body","message":"body must be an object"}}',
            content_type="application/json",
        )
    return body


def _wrap(handler: Callable[[web.Request], Awaitable[web.Response]]):
    async def wrapped(request: web.Request) -> web.Response:
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except SymphonyError as exc:
            # Includes WorkflowMutationError — exc.code/.message are shaped
            # for the UI ("workflow_mutation_error", verbatim reason).
            return _json_error(400, exc.code, exc.message)
        except Exception as exc:
            log.warning(
                "webapi_unhandled_error",
                path=request.path,
                method=request.method,
                error=str(exc),
            )
            return _json_error(500, "internal_error", str(exc))

    return wrapped


class _Ctx:
    """Per-request access to config, tracker and stats for one board."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def config(self) -> ServiceConfig:
        cfg = self.orchestrator.workflow_state.current()
        if cfg is None:
            cfg, err = self.orchestrator.workflow_state.reload()
            if cfg is None:
                raise WorkflowMutationError(f"workflow not loaded: {err}")
        return cfg

    def workflow_dir(self) -> Path:
        return self.config().workflow_path.parent

    def file_tracker(self) -> FileBoardTracker:
        cfg = self.config()
        if cfg.tracker.kind != "file":
            raise WorkflowMutationError(
                "board editing requires tracker.kind: file in WORKFLOW.md"
            )
        return FileBoardTracker(cfg.tracker)

    def stats(self) -> StatsStore:
        return stats_store_for(self.workflow_dir() / ".symphony" / "stats.jsonl")


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def _issue_card(
    issue: Issue,
    *,
    attention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "identifier": issue.identifier,
        "title": issue.title,
        "state": issue.state,
        "priority": issue.priority,
        "labels": list(issue.labels),
        "skills": list(issue.skills),
        "agent_kind": issue.agent_kind or "",
        "blocked_by": [
            {"identifier": b.identifier, "state": b.state} for b in issue.blocked_by
        ],
        "attention": attention,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return value


def _columns_payload(cfg: ServiceConfig) -> list[dict[str, Any]]:
    descriptions = {
        k.lower(): v for k, v in (cfg.tracker.state_descriptions or {}).items()
    }
    stage_paths = {k.lower(): str(v) for k, v in cfg.prompts.stage_paths.items()}
    out: list[dict[str, Any]] = []
    for name in cfg.tracker.active_states:
        out.append(
            {
                "name": name,
                "terminal": False,
                "description": descriptions.get(name.lower(), ""),
                "has_prompt": name.lower() in stage_paths,
            }
        )
    for name in cfg.tracker.terminal_states:
        out.append(
            {
                "name": name,
                "terminal": True,
                "description": descriptions.get(name.lower(), ""),
                "has_prompt": False,
            }
        )
    return out


def _continuous_improvement_payload(cfg: ServiceConfig) -> dict[str, Any]:
    ci = cfg.continuous_improvement
    return {
        "enabled": ci.enabled,
        "interval_ms": ci.interval_ms,
        "max_turns": ci.max_turns,
        "agent_kind": ci.agent_kind,
        "ticket_prefix": ci.ticket_prefix,
        "max_tickets_per_run": ci.max_tickets_per_run,
        "require_idle_board": ci.require_idle_board,
    }


def _workflow_payload(cfg: ServiceConfig) -> dict[str, Any]:
    return {
        "workflow_path": str(cfg.workflow_path),
        "columns": _columns_payload(cfg),
        "agent": {
            "kind": cfg.agent.kind,
            "max_concurrent_agents": cfg.agent.max_concurrent_agents,
            "max_turns": cfg.agent.max_turns,
            "max_attempts": cfg.agent.max_attempts,
            "feature_base_branch": cfg.agent.feature_base_branch,
            "auto_merge_target_branch": cfg.agent.auto_merge_target_branch,
            "auto_merge_on_done": cfg.agent.auto_merge_on_done,
        },
        "agent_kinds": sorted(SUPPORTED_AGENT_KINDS),
        "continuous_improvement": _continuous_improvement_payload(cfg),
        "polling_interval_ms": cfg.poll_interval_ms,
    }


def _live_by_identifier(orchestrator: Orchestrator) -> dict[str, dict[str, Any]]:
    snapshot = orchestrator.snapshot()
    live: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("running", []):
        live[row["issue_identifier"]] = {"status": "running", **row}
    for row in snapshot.get("retrying", []):
        identifier = row.get("issue_identifier") or row.get("identifier") or ""
        live[identifier] = {"status": "retrying", **row}
    return live


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _valid_states(cfg: ServiceConfig) -> dict[str, str]:
    """lowercase -> canonical casing for every configured state."""
    return {
        s.lower(): s
        for s in (*cfg.tracker.active_states, *cfg.tracker.terminal_states)
    }


def _check_identifier(raw: str) -> str:
    """Whitelist ticket identifiers before they touch the filesystem.

    `find_path` builds `board_root / f"{identifier}.md"`; on Windows a
    backslash in the identifier traverses directories, so every route
    parameter must pass this gate (not just creation).
    """
    identifier = (raw or "").strip()
    if not _IDENTIFIER_RE.match(identifier):
        raise WorkflowMutationError(
            "identifier must match ^[A-Za-z][A-Za-z0-9_-]{0,63}$"
        )
    return identifier


def _check_title(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkflowMutationError("title is required")
    title = raw.strip()
    if len(title) > _MAX_TITLE:
        raise WorkflowMutationError(f"title too long (max {_MAX_TITLE} chars)")
    return title


def _check_description(raw: Any) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise WorkflowMutationError("description must be a string")
    if len(raw) > _MAX_BODY:
        raise WorkflowMutationError(f"description too long (max {_MAX_BODY} chars)")
    return raw


def _check_priority(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise WorkflowMutationError("priority must be an integer 0-4 or null")
    if not 0 <= raw <= 4:
        raise WorkflowMutationError("priority must be between 0 and 4")
    return raw


def _check_labels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowMutationError("labels must be a list of strings")
    labels: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        label = item.strip().lower()
        if label and len(label) <= 50 and label not in labels:
            labels.append(label)
    if len(labels) > _MAX_LABELS:
        raise WorkflowMutationError(f"too many labels (max {_MAX_LABELS})")
    return labels


def _check_agent_kind(raw: Any) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise WorkflowMutationError("agent_kind must be a string")
    kind = raw.strip().lower()
    if kind and kind not in SUPPORTED_AGENT_KINDS:
        raise WorkflowMutationError(
            f"unknown agent_kind {kind!r}; supported: {sorted(SUPPORTED_AGENT_KINDS)}"
        )
    return kind


def _parse_ci_settings(body: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(body) - _CI_EDITABLE_KEYS)
    if unknown:
        raise WorkflowMutationError(
            f"unknown continuous_improvement field(s): {', '.join(unknown)}"
        )
    updates: dict[str, Any] = {}
    if "enabled" in body:
        value = body["enabled"]
        if not isinstance(value, bool):
            raise WorkflowMutationError("enabled must be a boolean")
        updates["enabled"] = value
    if "interval_ms" in body:
        value = body["interval_ms"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkflowMutationError("interval_ms must be an integer")
        updates["interval_ms"] = value
    if "max_turns" in body:
        value = body["max_turns"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkflowMutationError("max_turns must be an integer")
        updates["max_turns"] = value
    if "agent_kind" in body:
        updates["agent_kind"] = _check_agent_kind(body["agent_kind"])
    if not updates:
        raise WorkflowMutationError(
            "body must set enabled, interval_ms, max_turns, and/or agent_kind"
        )
    return updates


def _check_state(cfg: ServiceConfig, raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkflowMutationError("state is required")
    canonical = _valid_states(cfg).get(raw.strip().lower())
    if canonical is None:
        raise WorkflowMutationError(f"unknown state {raw.strip()!r}")
    return canonical


def _parse_state_specs(body: dict[str, Any]) -> list[StateSpec]:
    raw_states = body.get("states")
    if not isinstance(raw_states, list):
        raise WorkflowMutationError("body must contain a `states` list")
    specs: list[StateSpec] = []
    for raw in raw_states:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise WorkflowMutationError("each state needs at least a `name`")
        raw_description = raw.get("description")
        specs.append(
            StateSpec(
                name=raw["name"],
                # None = "not provided, keep the current description";
                # "" = explicit clear. See StateSpec.
                description=raw_description
                if isinstance(raw_description, str)
                else None,
                terminal=bool(raw.get("terminal")),
                previous_name=(
                    str(raw["previous_name"])
                    if isinstance(raw.get("previous_name"), str)
                    else None
                ),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# routes: board + issues
# ---------------------------------------------------------------------------


def _register_issue_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    async def handle_runs(request: web.Request) -> web.Response:
        raw_limit = request.query.get("limit", "50")
        try:
            limit = clamp_run_history_limit(int(raw_limit))
        except (TypeError, ValueError):
            limit = clamp_run_history_limit(50)
        issue_id = request.query.get("issue") or None
        runs, registry_error = orchestrator.recent_runs(issue_id=issue_id, limit=limit)
        payload: dict[str, Any] = {"runs": runs, "count": len(runs)}
        if registry_error:
            payload["registry_error"] = registry_error
        return web.json_response(payload)

    async def handle_board(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        issues: list[dict[str, Any]] = []
        read_only = cfg.tracker.kind != "file"
        if not read_only:
            tracker = FileBoardTracker(cfg.tracker)
            all_states = list(_valid_states(cfg).values())
            fetched = await asyncio.to_thread(tracker.fetch_issues_by_states, all_states)
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
                    "agent_kinds": sorted(SUPPORTED_AGENT_KINDS),
                },
                "columns": _columns_payload(cfg),
                "issues": issues,
                "live": _live_by_identifier(orchestrator),
            }
        )

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
        fields = {
            "title": title,
            "state": state,
            "priority": _check_priority(body.get("priority")),
            "labels": _check_labels(body.get("labels")),
            "description": _check_description(body.get("description")),
            "agent_kind": _check_agent_kind(body.get("agent_kind")) or None,
            "skills": list(normalize_skill_names(body.get("skills") or [])),
        }
        raw_identifier = body.get("identifier")
        if raw_identifier:
            if not isinstance(raw_identifier, str):
                raise WorkflowMutationError("identifier must be a string")
            identifier = _check_identifier(raw_identifier)
            await asyncio.to_thread(tracker.create, identifier=identifier, **fields)
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
                tracker.create_with_next_identifier, prefix, **fields
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
        return web.json_response(
            {
                **card,
                "description": body_text,
                "frontmatter": _json_safe(front),
                "live": live,
            }
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
            fields["agent_kind"] = _check_agent_kind(body.get("agent_kind"))
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
        await asyncio.to_thread(tracker.update_fields, identifier, **fields)
        if new_state is not None and new_state.lower() != current.state.lower():
            await asyncio.to_thread(
                ctx.stats().record_transition,
                issue=identifier,
                from_state=current.state.lower(),
                to_state=new_state.lower(),
            )
        orchestrator.request_refresh()
        return web.json_response({"identifier": identifier, "updated": sorted(fields)})

    async def handle_issue_recover_blocked(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        body = await _read_json(request)
        raw_target = body.get("rca_state", body.get("target_state"))
        if raw_target is not None and not isinstance(raw_target, str):
            raise WorkflowMutationError("rca_state must be a string")
        agent_kind = _check_agent_kind(body.get("agent_kind")) if "agent_kind" in body else None
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

    async def handle_issue_skip_learn(request: web.Request) -> web.Response:
        identifier = _check_identifier(request.match_info["identifier"])
        changed, message = await orchestrator.skip_learn(identifier)
        if not changed:
            status = 404 if message.startswith("unknown issue") else 409
            return _json_error(status, "learn_skip_rejected", message)
        return web.json_response(
            {"identifier": identifier, "skipped": True, "message": message}
        )

    app.router.add_get("/api/v1/runs", _wrap(handle_runs))
    app.router.add_get("/api/v1/board", _wrap(handle_board))
    app.router.add_post("/api/v1/issues", _wrap(handle_issue_create))
    app.router.add_get("/api/v1/issues/{identifier}", _wrap(handle_issue_detail))
    app.router.add_patch("/api/v1/issues/{identifier}", _wrap(handle_issue_patch))
    app.router.add_post(
        "/api/v1/issues/{identifier}/recover-blocked",
        _wrap(handle_issue_recover_blocked),
    )
    app.router.add_delete("/api/v1/issues/{identifier}", _wrap(handle_issue_delete))
    app.router.add_post(
        "/api/v1/issues/{identifier}/skip-learn", _wrap(handle_issue_skip_learn)
    )


# ---------------------------------------------------------------------------
# routes: workflow (columns + prompts + branch policy)
# ---------------------------------------------------------------------------


def _register_workflow_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    async def handle_workflow_get(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        return web.json_response(_workflow_payload(cfg))

    async def handle_states_put(request: web.Request) -> web.Response:
        body = await _read_json(request)
        specs = _parse_state_specs(body)
        cfg = ctx.config()
        tracker = ctx.file_tracker()
        # A running worker owns its ticket's state string — refuse edits
        # that would rename or remove that state under it. (Best-effort:
        # a worker dispatched during the write below is handled by the
        # orchestrator's normal mid-run state reconciliation.)
        running_states = {
            i.state.lower() for i in orchestrator.iter_running_issues()
        }
        new_names = {s.name.lower() for s in specs}
        rename_sources = {
            (s.previous_name or "").lower() for s in specs if s.previous_name
        }
        for state in running_states:
            if state not in new_names or state in rename_sources:
                return _json_error(
                    409,
                    "state_in_use",
                    f"column {state!r} has a running worker; wait or pause first",
                )

        plan = await asyncio.to_thread(apply_states_update, cfg.workflow_path, specs)
        # Migrate tickets before the next poll sees the new config. Skip any
        # ticket whose worker started while the write was in flight.
        migrated: dict[str, str] = {}
        skipped: list[str] = []
        moves = [(old, new) for old, new in plan.renamed.items()]
        moves.extend((old, plan.fallback_state) for old in plan.removed)
        for old, target in moves:
            for issue in await asyncio.to_thread(
                tracker.fetch_issues_by_states, [old]
            ):
                if orchestrator.find_running_issue_id(issue.identifier) is not None:
                    skipped.append(issue.identifier)
                    continue
                await asyncio.to_thread(tracker.transition, issue.identifier, target)
                migrated[issue.identifier] = target
        orchestrator.workflow_state.reload()
        orchestrator.request_refresh()
        return web.json_response(
            {
                "renamed": plan.renamed,
                "removed": plan.removed,
                "added": plan.added,
                "migrated": migrated,
                "skipped_running": skipped,
            }
        )

    async def handle_prompt_get(request: web.Request) -> web.Response:
        state = request.match_info["state"]
        cfg = ctx.config()
        payload = await asyncio.to_thread(read_prompt, cfg.workflow_path, state)
        if payload is None:
            return _json_error(
                404, "prompt_not_configured", f"no prompt file for state {state!r}"
            )
        return web.json_response(payload)

    async def handle_prompt_put(request: web.Request) -> web.Response:
        state = request.match_info["state"]
        body = await _read_json(request)
        content = body.get("content")
        if not isinstance(content, str):
            raise WorkflowMutationError("body must contain string `content`")
        cfg = ctx.config()
        path = await asyncio.to_thread(write_prompt, cfg.workflow_path, state, content)
        orchestrator.workflow_state.reload()
        return web.json_response(
            {"state": state, "path": str(path), "bytes": len(content.encode("utf-8"))}
        )

    async def handle_branch_policy_put(request: web.Request) -> web.Response:
        body = await _read_json(request)
        updates: dict[str, str] = {}
        for key in ("feature_base_branch", "auto_merge_target_branch"):
            if key in body:
                value = body.get(key)
                if not isinstance(value, str):
                    raise WorkflowMutationError(f"{key} must be a string")
                value = value.strip()
                if value and not _BRANCH_RE.match(value):
                    raise WorkflowMutationError(f"invalid branch name {value!r}")
                updates[key] = value
        if not updates:
            raise WorkflowMutationError(
                "body must set feature_base_branch and/or auto_merge_target_branch"
            )
        cfg = ctx.config()
        await asyncio.to_thread(
            set_branch_policy,
            cfg.workflow_path,
            feature_base_branch=updates.get("feature_base_branch"),
            auto_merge_target_branch=updates.get("auto_merge_target_branch"),
        )
        orchestrator.workflow_state.reload()
        return web.json_response({"updated": sorted(updates)})

    async def handle_continuous_improvement_put(
        request: web.Request,
    ) -> web.Response:
        body = await _read_json(request)
        updates = _parse_ci_settings(body)
        cfg = ctx.config()
        await asyncio.to_thread(
            set_continuous_improvement_settings,
            cfg.workflow_path,
            enabled=updates.get("enabled"),
            interval_ms=updates.get("interval_ms"),
            max_turns=updates.get("max_turns"),
            agent_kind=updates.get("agent_kind"),
        )
        new_cfg, err = orchestrator.workflow_state.reload()
        if new_cfg is None:
            raise WorkflowMutationError(f"workflow not loaded: {err}")
        orchestrator.request_refresh()
        return web.json_response(
            {
                "updated": sorted(updates),
                "continuous_improvement": _continuous_improvement_payload(new_cfg),
            }
        )

    async def handle_continuous_improvement_reset(
        request: web.Request,
    ) -> web.Response:
        if request.body_exists:
            await _read_json(request)
        orchestrator.reset_continuous_improvement_turns()
        return web.json_response(
            {"status": orchestrator.continuous_improvement_status()}
        )

    async def handle_continuous_improvement_status(
        _request: web.Request,
    ) -> web.Response:
        return web.json_response(orchestrator.continuous_improvement_status())

    async def handle_git_branches(_request: web.Request) -> web.Response:
        import subprocess

        def _branches() -> list[str]:
            try:
                proc = subprocess.run(
                    ["git", "branch", "--format=%(refname:short)"],
                    cwd=ctx.workflow_dir(),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                return []
            if proc.returncode != 0:
                return []
            return [b.strip() for b in proc.stdout.splitlines() if b.strip()]

        return web.json_response({"branches": await asyncio.to_thread(_branches)})

    app.router.add_get("/api/v1/workflow", _wrap(handle_workflow_get))
    app.router.add_put("/api/v1/workflow/states", _wrap(handle_states_put))
    app.router.add_get("/api/v1/workflow/prompts/{state}", _wrap(handle_prompt_get))
    app.router.add_put("/api/v1/workflow/prompts/{state}", _wrap(handle_prompt_put))
    app.router.add_put("/api/v1/workflow/branch-policy", _wrap(handle_branch_policy_put))
    app.router.add_put(
        "/api/v1/workflow/continuous-improvement",
        _wrap(handle_continuous_improvement_put),
    )
    app.router.add_post(
        "/api/v1/workflow/continuous-improvement/reset-turns",
        _wrap(handle_continuous_improvement_reset),
    )
    app.router.add_get(
        "/api/v1/continuous-improvement/status",
        _wrap(handle_continuous_improvement_status),
    )
    app.router.add_get("/api/v1/git/branches", _wrap(handle_git_branches))


# ---------------------------------------------------------------------------
# routes: stats + static SPA
# ---------------------------------------------------------------------------


def _register_meta_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    async def handle_stats(request: web.Request) -> web.Response:
        try:
            days = int(request.query.get("days", "30"))
        except ValueError:
            return _json_error(400, "invalid_days", "days must be an integer")
        days = max(1, min(days, 365))
        cfg = ctx.config()
        # Completion = arrival in "Done" when the board has one; otherwise
        # any terminal state that is not a parking lane.
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        skip = {cfg.tracker.archive_state.lower(), "cancelled", "blocked"}
        done_states = {"done"} if "done" in terminal else (terminal - skip or {"done"})
        aggregated = await asyncio.to_thread(ctx.stats().aggregate, days, done_states)
        snapshot = orchestrator.snapshot()
        aggregated["live"] = {
            "running": snapshot["counts"]["running"],
            "retrying": snapshot["counts"]["retrying"],
            "session_totals": snapshot["codex_totals"],
        }
        return web.json_response(aggregated)

    async def handle_index(_request: web.Request) -> web.StreamResponse:
        index = STATIC_DIR / "index.html"
        if not index.exists():
            return web.Response(
                text="symphony web UI assets missing; reinstall the package",
                status=503,
            )
        return web.FileResponse(index)

    app.router.add_get("/api/v1/stats", _wrap(handle_stats))
    app.router.add_get("/", handle_index)
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR, show_index=False)


def register_web_routes(app: web.Application, orchestrator: Orchestrator) -> None:
    ctx = _Ctx(orchestrator)
    app.middlewares.append(_api_guard)
    _register_issue_routes(app, ctx, orchestrator)
    _register_workflow_routes(app, ctx, orchestrator)
    _register_meta_routes(app, ctx, orchestrator)
