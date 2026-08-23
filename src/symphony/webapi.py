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
Setting `SYMPHONY_API_TOKEN` adds a credential layer on top: every operator
`/api/` request (reads included) must then present it as a bearer token, so an
exposed bind does not hand full board control to any network peer. The exact
managed-service health probe may instead present its separate per-launch
capability header; that capability authorizes only `/api/v1/health`. The
shipped SPA supports operator-token mode by prompting for the token and sending
`Authorization: Bearer` on every fetch. The chat WebSocket is the only route
where the operator token may also arrive as a `?token=` query parameter,
because browsers cannot set headers on a WebSocket handshake (the query
exception is scoped to that one route — query strings leak into access logs,
so no other endpoint accepts it).
Fronting the board with a reverse proxy or tunnel is the other opt-in: the
public name goes in `SYMPHONY_TRUSTED_ORIGINS` so project mutations and the
chat WebSocket accept it.
"""

from __future__ import annotations

import asyncio
import heapq
import hmac
import ipaddress
import json
import os
import re
from functools import partial
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlsplit

from aiohttp import WSCloseCode, WSMsgType, web

from .chat import ChatManager
from .errors import (
    ChatBackendUnavailableError,
    ChatBusyError,
    ChatNoSessionError,
    ChatProjectActionError,
    ChatProjectAuthorizationError,
    ChatSessionExistsError,
    ConfigValidationError,
    SymphonyError,
)
from .issue import Issue, registration_order_key
from .logging import get_logger
from .service_identity import (
    SERVICE_INSTANCE_HEADER,
    normalize_service_probe_credential,
)
from .skills import normalize_skill_names
from .artifacts import ArtifactRecord, ArtifactStore
from .stats import StatsStore, stats_store_for
from .trackers import context_manager as tracker_context_manager
from .trackers.file import FileBoardTracker, parse_ticket_file
from .trackers.validate import (
    IDENTIFIER_RE as _IDENTIFIER_RE,
    IDENTIFIER_RULE as _IDENTIFIER_RULE,
    validate_ticket_dependencies,
)
from .utils import git_inspect, git_ops
from .utils.auto_merge import auto_merge_on_done_best_effort
from .utils.git_ops import GitOpResult
from .orchestrator import Orchestrator
from .product_preview import ProductPreviewError, ProductPreviewManager
from .projects import (
    Project,
    ProjectError,
    ProjectRegistry,
    ProjectTargetExpectation,
    canonical_project_repo,
)
from .orchestrator.run_registry import clamp_run_history_limit
from .orchestrator.scheduler import (
    MAX_DEPENDENCY_EDGES,
    MAX_DEPENDENCY_NODES,
    RequestGroupKey,
    dependency_cycle_nodes,
    group_by_request,
)
from .workflow import (
    SUPPORTED_AGENT_KINDS,
    SUPPORTED_CI_MODES,
    SYMPHONY_BRANCH_PREFIX,
    ServiceConfig,
    validated_ci_modes,
)
from .workflow.mutate import (
    StateSpec,
    WorkflowMutationError,
    apply_lane_preset,
    apply_states_update,
    read_prompt,
    set_branch_policy,
    set_continuous_improvement_settings,
    write_prompt,
)
from .workflow.preflight import stage_turn_budget_error
from .workflow.presets import LANE_PRESETS, get_lane_preset, guess_lane_preset

log = get_logger()

STATIC_DIR = Path(__file__).parent / "web" / "static"

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
# `ChatManager` mints these as <UTC date>-<UTC time>-<6 hex>.
_CHAT_SESSION_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_PROJECT_SETUP_ACTION_RE = re.compile(r"^project-[0-9a-f]{32}$")
_CHAT_CONFIRMATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_TITLE = 300
_MAX_BODY = 128_000
_MAX_LABELS = 20
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
_LOOPBACK_BINDS = {"", "localhost", "127.0.0.1", "::1", "[::1]"}
# Operators who front the board with a reverse proxy or tunnel (cloudflared,
# ngrok, ssh -L with a rewritten Host) reach it under a public name the
# loopback allowlists cannot know. They declare it here, comma separated:
#   SYMPHONY_TRUSTED_ORIGINS=https://symphony.example.com
# Entries may be full origins (`https://host:port`), bare hostnames (any
# scheme/port), or `*` to trust every origin.
TRUSTED_ORIGINS_ENV = "SYMPHONY_TRUSTED_ORIGINS"
# Optional credential gate for operator requests across the `/api/` surface.
# Unset/empty keeps the frictionless loopback default. The one non-operator
# path is the exact managed-service health probe, authenticated by its separate
# per-launch capability and scoped to `/api/v1/health` below.
API_TOKEN_ENV = "SYMPHONY_API_TOKEN"
# The one route where the token may also arrive as `?token=`: browsers
# cannot set an Authorization header on a WebSocket handshake, so the SPA
# appends the query parameter there. Scoped to the exact path — anywhere
# else a query-supplied token must stay a 401 because query strings are
# the part of a URL most likely to end up in access logs.
_CHAT_WS_PATH = "/api/v1/chat/ws"
_HEALTH_PATH = "/api/v1/health"
_CI_EDITABLE_KEYS = {"enabled", "interval_ms", "max_turns", "agent_kind", "modes"}
BIND_HOST_KEY: web.AppKey[str] = web.AppKey("symphony.bind_host", str)
CHAT_MANAGER_KEY: web.AppKey[ChatManager] = web.AppKey("symphony.chat", ChatManager)
SERVICE_INSTANCE_ID_KEY: web.AppKey[str | None] = web.AppKey(
    "symphony.service_instance_id"
)
_MAX_CHAT_MESSAGE = 32_000


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}}, status=status
    )


# ---------------------------------------------------------------------------
# request plumbing
# ---------------------------------------------------------------------------


def _request_is_loopback(request: web.Request) -> bool:
    """Keep sensitive run diagnostics on the operator's local machine."""
    remote = request.remote
    if remote is None:
        bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
        return bind in _LOOPBACK_BINDS
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def _request_host(request: web.Request) -> str:
    """Host header without the port — bracket-aware for IPv6 literals."""
    raw = (request.host or "").strip().lower()
    if raw.startswith("["):
        return raw.split("]", 1)[0] + "]"
    return raw.rsplit(":", 1)[0]


def _bare_host(host: str) -> str:
    """Strip IPv6 brackets so Host and Origin hostnames compare equal."""
    return host.strip().lower().removeprefix("[").removesuffix("]")


def _host_is_loopback(host: str) -> bool:
    bare = _bare_host(host)
    if bare == "localhost":
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def _trusted_origins() -> set[str]:
    """Operator-declared origins from `SYMPHONY_TRUSTED_ORIGINS`."""
    raw = os.environ.get(TRUSTED_ORIGINS_ENV, "")
    return {
        entry.strip().lower().rstrip("/")
        for entry in raw.replace(";", ",").split(",")
        if entry.strip()
    }


def _host_is_declared_trusted(host: str) -> bool:
    """Does `SYMPHONY_TRUSTED_ORIGINS` name this Host header?"""
    trusted = _trusted_origins()
    if "*" in trusted:
        return True
    bare = _bare_host(host)
    if not bare:
        return False
    return any(
        bare == _bare_host(urlsplit(entry).hostname or entry) for entry in trusted
    )


def _origin_is_trusted(request: web.Request, origin: str) -> bool:
    """Is this browser Origin allowed to mutate this board?

    A missing Origin is fine — non-browser clients omit it, and browsers
    always send one on the cross-origin requests we care about. Anything
    else has to be loopback, the very host the browser addressed, or an
    origin the operator declared.
    """
    if not origin:
        return True
    trusted = _trusted_origins()
    if "*" in trusted:
        return True
    normalized = origin.strip().lower().rstrip("/")
    if normalized in trusted:
        return True
    host = _bare_host(urlsplit(origin).hostname or "")
    if not host:
        # `Origin: null` — sandboxed iframe, file://, or a redirected form post.
        return False
    if host in trusted:
        return True
    if _host_is_loopback(host):
        # Same machine. A TLS-terminating proxy or port forward in front of
        # the board changes the scheme or port but not the trust boundary.
        return True
    return host == _bare_host(_request_host(request))


def _configured_api_token() -> str | None:
    """Bearer token from `SYMPHONY_API_TOKEN`, or None when unset/blank."""
    token = os.environ.get(API_TOKEN_ENV, "").strip()
    return token or None


def _request_has_valid_bearer(request: web.Request, token: str) -> bool:
    """`Authorization: Bearer <token>` exact match, in constant time.

    Both sides are compared as UTF-8 bytes: `hmac.compare_digest` rejects
    non-ASCII `str` inputs, and a client may send any octet sequence.
    """
    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].encode("utf-8"), token.encode("utf-8"))


def _request_has_valid_ws_query_token(request: web.Request, token: str) -> bool:
    """`?token=<token>` exact match for the chat WS handshake, constant time.

    Same comparison discipline as the bearer helper. Only ever consulted
    for `_CHAT_WS_PATH` by the API guard.
    """
    supplied = request.query.get("token", "")
    return hmac.compare_digest(
        supplied.encode("utf-8"), token.encode("utf-8")
    )


def _request_has_valid_service_instance(
    request: web.Request, expected_instance_id: str | None
) -> bool:
    """Authenticate only the managed service's exact health probe."""
    expected = normalize_service_probe_credential(expected_instance_id)
    supplied = normalize_service_probe_credential(
        request.headers.get(SERVICE_INSTANCE_HEADER)
    )
    if expected is None or supplied is None:
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    )


@web.middleware
async def _api_guard(request: web.Request, handler):
    if request.path.startswith("/api/"):
        # Optional credential layer: reads leak the board too, so every
        # operator request must present the API token. The only alternate
        # capability is the managed service's exact health probe below.
        # Order matters — authenticate before validating anything else.
        api_token = _configured_api_token()
        if api_token is not None and not _request_has_valid_bearer(
            request, api_token
        ):
            # Browsers cannot set headers on a WebSocket handshake; the
            # chat socket alone may also present the token as ?token=.
            ws_authorized = request.path == _CHAT_WS_PATH and (
                _request_has_valid_ws_query_token(request, api_token)
            )
            health_authorized = request.path == _HEALTH_PATH and (
                _request_has_valid_service_instance(
                    request, request.app.get(SERVICE_INSTANCE_ID_KEY)
                )
            )
            if not ws_authorized and not health_authorized:
                return _json_error(
                    401, "unauthorized", "missing or invalid bearer token"
                )
        bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
        host = _request_host(request)
        if (
            bind in _LOOPBACK_BINDS
            and host not in _ALLOWED_HOSTS
            # A proxy that forwards the public Host verbatim is still the
            # operator's own front door once they have declared it.
            and not _host_is_declared_trusted(host)
        ):
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


def _wrap(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    # StreamResponse (not Response) so file-serving routes can hand back a
    # web.FileResponse; the error paths below still return plain Responses,
    # which are StreamResponses.
    async def wrapped(request: web.Request) -> web.StreamResponse:
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
            # Never echo internal exception text (paths, SQL, library
            # internals) to HTTP clients — the log line above keeps the
            # detail for the operator.
            return _json_error(500, "internal_error", "internal server error")

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

    def artifacts(self) -> ArtifactStore | None:
        """Read-side view of the ticket artifact store, or None when off.

        Collection is the orchestrator's job, so the size caps do not apply
        here. Not strictly read-only: a corrupt index is rebuilt (and
        rewritten) on read. That write is `os.replace`, so a reader never
        sees a torn index even though this instance's lock is not the
        orchestrator's.
        """
        cfg = self.config()
        if not cfg.artifacts.enabled:
            return None
        return ArtifactStore(self.workflow_dir() / ".symphony" / "artifacts")


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
        # Audit stamp on `stage_kinds`-routed boards, where the pin stays
        # empty on purpose (F-20).
        "last_agent_kind": issue.last_agent_kind or "",
        "request": issue.request or "",
        "blocked_by": [
            {"identifier": b.identifier, "state": b.state} for b in issue.blocked_by
        ],
        "attention": attention,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
    }


_PUBLIC_SCHEDULE_REASONS = {
    "not_evaluated": "scheduler evaluation is pending",
    "ready": "eligible for dispatch",
    "dispatched": "agent run started",
    "running": "agent run is active",
    "retry_scheduled": "owned by the retry timer",
    "auto_triage": "ticket advanced by automatic triage",
    "continuous_improvement": "continuous improvement owns the idle board",
    "leased_elsewhere": "owned by another durable run lease",
    "registry_unavailable": "durable authority registry is unavailable",
    "historical_release_verifier": "historical release verifier is evidence-only",
    "claimed": "another scheduler path owns the ticket",
    "paused": "ticket is paused",
    "budget_exhausted": "turn budget is exhausted",
    "finalizing": "finalization owns the ticket",
    "inactive": "ticket state is not active",
    "incomplete_identity": "ticket identity is incomplete",
    "unsupported_agent": "ticket requests an unsupported agent",
    "waiting_dependency": "waiting for dependencies",
    "waiting_global_capacity": "waiting for global capacity",
    "waiting_state_capacity": "waiting for state capacity",
    "refused_conflict": "final conflict check refused dispatch",
    "refused_dispatch_authority": "final dispatch authority was not acquired",
    "terminal_success": "dependency is complete",
    "terminal_needs_action": "terminal state does not resolve dependencies",
    "dangling_dependency": "blocker is not present on the board",
    "snapshot_unavailable": "not present in the last scheduler evaluation",
    "decision_stale": "ticket changed after scheduler evaluation",
}


def _public_schedule_reason(code: str) -> str:
    return _PUBLIC_SCHEDULE_REASONS.get(code, "scheduler decision is available by code")


def _request_group_schedule_payload(
    *,
    key: RequestGroupKey,
    members: list[Issue],
    all_issues: list[Issue],
    schedule: dict[str, Any],
    orchestrator: Orchestrator,
    cfg: ServiceConfig,
) -> dict[str, Any]:
    by_identifier = {issue.identifier: issue for issue in all_issues}
    by_reference = dict(by_identifier)
    by_reference.update({issue.id: issue for issue in all_issues})
    decisions = {
        row.get("identifier"): row
        for row in schedule.get("entries", [])
        if isinstance(row, dict) and isinstance(row.get("identifier"), str)
    }
    member_ids = {issue.identifier for issue in members}
    node_issues: dict[str, Issue | None] = {
        issue.identifier: issue for issue in members
    }
    edge_set: set[tuple[str, str]] = set()
    pending = list(members)
    traversed: set[str] = set()
    while pending:
        issue = pending.pop()
        if issue.identifier in traversed:
            continue
        traversed.add(issue.identifier)
        node_issues.setdefault(issue.identifier, issue)
        for blocker in issue.blocked_by:
            blocker_reference = blocker.identifier or blocker.id
            if not blocker_reference:
                continue
            blocker_issue = by_reference.get(blocker_reference)
            blocker_id = (
                blocker_issue.identifier
                if blocker_issue is not None
                else (blocker.identifier or blocker.id)
            )
            if not blocker_id:
                continue
            edge_set.add((blocker_id, issue.identifier))
            node_issues.setdefault(blocker_id, blocker_issue)
            if blocker_issue is not None and blocker_issue.identifier not in traversed:
                pending.append(blocker_issue)

    downstream: dict[str, list[str]] = {identifier: [] for identifier in node_issues}
    indegree = {identifier: 0 for identifier in node_issues}
    for blocker_id, dependent_id in sorted(edge_set):
        downstream.setdefault(blocker_id, []).append(dependent_id)
        indegree.setdefault(dependent_id, 0)
        indegree[dependent_id] += 1
    ready = [identifier for identifier, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    execution_order: list[str] = []
    while ready:
        identifier = heapq.heappop(ready)
        execution_order.append(identifier)
        for dependent in sorted(downstream.get(identifier, [])):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    warnings: list[str] = []
    unordered = sorted(set(node_issues) - set(execution_order))
    cycle_nodes = dependency_cycle_nodes(set(node_issues), edge_set)
    if unordered:
        execution_order.extend(unordered)
    if cycle_nodes:
        warnings.append("dependency_cycle")

    terminal_states = {state.strip().lower() for state in cfg.tracker.terminal_states}
    counts = {
        "running": 0,
        "ready": 0,
        "waiting": 0,
        "retrying": 0,
        "successful": 0,
        "needs_action": 0,
    }
    nodes: list[dict[str, Any]] = []
    for identifier in execution_order:
        issue = node_issues[identifier]
        scope = "request" if identifier in member_ids else "external"
        decision = decisions.get(identifier)
        if decision is None and issue is not None:
            decision = decisions.get(issue.identifier)
        if decision is not None:
            status = str(decision.get("status") or "waiting")
            code = str(decision.get("code") or "not_evaluated")
            reason = str(decision.get("reason") or "not evaluated")
        elif issue is None:
            status = "needs_action"
            code = "dangling_dependency"
            reason = "blocker is not present on the board"
            warnings.append(f"dangling_dependency:{identifier}")
        elif issue.state.strip().lower() in terminal_states:
            if orchestrator.dependency_state_resolved(issue.state):
                status = "successful"
                code = "terminal_success"
                reason = "dependency is complete"
            else:
                status = "needs_action"
                code = "terminal_needs_action"
                reason = f"terminal state does not resolve dependencies: {issue.state}"
        else:
            status = "waiting"
            code = "snapshot_unavailable"
            reason = "not present in the last scheduler evaluation"
        evaluated_state = decision.get("evaluated_state") if decision else None
        evaluated_updated_at = (
            decision.get("evaluated_updated_at") if decision else None
        )
        current_updated_at = (
            issue.updated_at.isoformat()
            if issue is not None and issue.updated_at is not None
            else None
        )
        current_blockers = (
            [
                {
                    "id": blocker.id,
                    "identifier": blocker.identifier,
                    "state": blocker.state,
                }
                for blocker in issue.blocked_by
            ]
            if issue is not None
            else []
        )
        decision_drifted = bool(
            issue is not None
            and (
                (evaluated_state is not None and issue.state != evaluated_state)
                or (
                    evaluated_updated_at is not None
                    and current_updated_at != evaluated_updated_at
                )
                or (
                    decision is not None
                    and "evaluated_priority" in decision
                    and issue.priority != decision.get("evaluated_priority")
                )
                or (
                    decision is not None
                    and "evaluated_request" in decision
                    and ((issue.request or "").strip() or None)
                    != decision.get("evaluated_request")
                )
                or (
                    decision is not None
                    and "evaluated_blocked_by" in decision
                    and current_blockers != decision.get("evaluated_blocked_by")
                )
                or (
                    decision is None
                    and bool(schedule.get("available"))
                    and issue.state.strip().lower() not in terminal_states
                )
            )
        )
        if decision_drifted:
            status = "needs_action"
            code = "decision_stale"
        reason = _public_schedule_reason(code)
        if scope == "request":
            counts[status if status in counts else "waiting"] += 1
        blockers: list[dict[str, Any]] = []
        for blocker, dependent in sorted(edge_set):
            if dependent != identifier:
                continue
            blocker_issue = node_issues.get(blocker)
            blockers.append(
                {
                    "identifier": blocker,
                    "state": blocker_issue.state if blocker_issue is not None else None,
                    "resolved": (
                        orchestrator.dependency_state_resolved(blocker_issue.state)
                        if blocker_issue is not None
                        else False
                    ),
                }
            )
        node = {
            "identifier": identifier,
            "title": issue.title if issue is not None else identifier,
            "exists": issue is not None,
            "state": issue.state if issue is not None else None,
            "priority": issue.priority if issue is not None else None,
            "scope": scope,
            "cycle": identifier in cycle_nodes,
            "decision_drifted": decision_drifted,
            "evaluated_state": evaluated_state,
            "blocked_by": blockers,
            "unlocks": sorted(downstream.get(identifier, [])),
            "decision": {"status": status, "code": code, "reason": reason},
            "queue_rank": decision.get("queue_rank") if decision else None,
            "scan_position": decision.get("scan_position") if decision else None,
            "wave": decision.get("wave") if decision else None,
            "global_critical_path_length": (
                decision.get("critical_path_length") if decision else None
            ),
            "starvation_promoted": bool(
                decision.get("starvation_promoted") if decision else False
            ),
            "retry": decision.get("retry") if decision else None,
        }
        nodes.append(node)

    longest_unresolved_nodes: int | None
    if cycle_nodes:
        longest_unresolved_nodes = None
    else:
        unresolved = {
            node["identifier"]
            for node in nodes
            if node["decision"]["status"] != "successful"
        }
        chain_nodes: dict[str, int] = {}
        for identifier in reversed(execution_order):
            if identifier not in unresolved:
                continue
            chain_nodes[identifier] = 1 + max(
                (
                    chain_nodes.get(dependent, 0)
                    for dependent in downstream.get(identifier, [])
                    if dependent in unresolved
                ),
                default=0,
            )
        longest_unresolved_nodes = max(chain_nodes.values(), default=0)
    has_decision_drift = any(node["decision_drifted"] for node in nodes)
    return {
        "schema_version": 1,
        "available": bool(schedule.get("available")),
        "request": {"kind": key.kind, "id": key.value},
        "generated_at": schedule.get("generated_at"),
        "stale": bool(schedule.get("stale")) or has_decision_drift,
        "decision_drifted": has_decision_drift,
        "execution_valid": not cycle_nodes,
        "policy": schedule.get("policy", "fifo"),
        "policy_order": schedule.get("policy_order", "starvation, registration"),
        "slots": schedule.get("slots", {}),
        "summary": {
            "counts": counts,
            "longest_unresolved_chain_nodes": longest_unresolved_nodes,
        },
        "nodes": nodes,
        "edges": [
            {"from": blocker, "to": dependent}
            for blocker, dependent in sorted(edge_set)
        ],
        "warnings": sorted(set(warnings)),
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
        "modes": list(ci.modes),
        "resolved_modes": list(ci.resolved_modes()),
        "supported_modes": list(SUPPORTED_CI_MODES),
        "mode_interval_hours": {
            mode: ci.interval_hours_for(mode) for mode in SUPPORTED_CI_MODES
        },
        "max_improvement_tickets_per_run": ci.max_improvement_tickets_per_run,
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
            "scheduling_policy": cfg.agent.scheduling_policy,
            "feature_base_branch": cfg.agent.feature_base_branch,
            "auto_merge_target_branch": cfg.agent.auto_merge_target_branch,
            "auto_merge_on_done": cfg.agent.auto_merge_on_done,
            "auto_merge_push_target": cfg.agent.auto_merge_push_target,
            "merge_delivery": (
                "upstream-publishing"
                if cfg.agent.auto_merge_push_target
                else "local-only"
            ),
            # F-06: the mechanical evidence floor is lane-name gated by
            # default, so the UI must be able to say when it is off.
            "stage_contracts": cfg.agent.stage_contracts,
            "stage_contracts_enabled": cfg.agent.stage_contracts_enabled(
                cfg.tracker.active_states
            ),
        },
        "agent_kinds": sorted(SUPPORTED_AGENT_KINDS),
        "continuous_improvement": _continuous_improvement_payload(cfg),
        "preview": {
            "enabled": cfg.preview.enabled,
            "cwd": cfg.preview.cwd,
            "health_path": cfg.preview.health_path,
            "url_path": cfg.preview.url_path,
            "release_ticket": cfg.preview.release_ticket,
            "acceptance": list(cfg.preview.acceptance),
        },
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
        s.lower(): s for s in (*cfg.tracker.active_states, *cfg.tracker.terminal_states)
    }


# Content types the board may render in-document. Deliberately excludes
# text/html and image/svg+xml — both can execute script against the board's
# own origin, and workers author these bytes.
_INLINE_ARTIFACT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "application/pdf",
        "text/plain",
    }
)


def _ascii_filename(name: str) -> str:
    """Header-safe filename: no quotes, no control chars, ASCII only."""
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in '"\\')
    return cleaned.encode("ascii", "replace").decode("ascii") or "artifact"


def _artifact_json(identifier: str, record: ArtifactRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "title": record.title,
        "summary": record.summary,
        "content_type": record.content_type,
        "byte_size": record.byte_size,
        "collected_at": record.collected_at,
        "run_id": record.run_id,
        "turn": record.turn,
        "inline": record.content_type in _INLINE_ARTIFACT_TYPES,
        "url": f"/api/v1/issues/{identifier}/artifacts/{quote(record.name)}",
    }


def _check_identifier(raw: str) -> str:
    """Whitelist ticket identifiers before they touch the filesystem.

    `find_path` builds `board_root / f"{identifier}.md"`; on Windows a
    backslash in the identifier traverses directories, so every route
    parameter must pass this gate (not just creation).
    """
    identifier = (raw or "").strip()
    if not _IDENTIFIER_RE.match(identifier):
        raise WorkflowMutationError(f"identifier must match {_IDENTIFIER_RULE}")
    return identifier


def _check_branch(raw: Any, *, key: str = "branch") -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkflowMutationError(f"{key} is required")
    branch = raw.strip()
    if not _BRANCH_RE.match(branch):
        raise WorkflowMutationError(f"invalid branch name {branch!r}")
    return branch


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


def _check_request(raw: Any) -> str:
    """Optional request grouping id (e.g. REQ-1); empty string clears."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise WorkflowMutationError("request must be a string")
    request = raw.strip()
    if request and not _IDENTIFIER_RE.match(request):
        raise WorkflowMutationError(f"request must match {_IDENTIFIER_RULE}")
    return request


def _check_request_schedule_key(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkflowMutationError("request schedule id is required")
    value = raw.strip()
    if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WorkflowMutationError("request schedule id is invalid")
    return value


def _check_blocked_by(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowMutationError("blocked_by must be a list of ticket identifiers")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise WorkflowMutationError(
                "blocked_by must be a list of ticket identifiers"
            )
        identifier = _check_identifier(item)
        if identifier not in out:
            out.append(identifier)
    return out


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


def _check_chat_session_id(raw: Any) -> str:
    """Session ids index into `.symphony/chat/<id>.jsonl` — validate strictly."""
    session_id = raw.strip() if isinstance(raw, str) else ""
    if not _CHAT_SESSION_RE.match(session_id):
        raise WorkflowMutationError(f"invalid chat session id {session_id!r}")
    return session_id


def _check_project_setup_action_id(raw: Any) -> str:
    action_id = raw.strip() if isinstance(raw, str) else ""
    if not _PROJECT_SETUP_ACTION_RE.fullmatch(action_id):
        raise WorkflowMutationError(f"invalid project setup action id {action_id!r}")
    return action_id


def _check_chat_confirmation_token(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not _CHAT_CONFIRMATION_TOKEN_RE.fullmatch(raw):
        raise WorkflowMutationError("invalid chat confirmation token")
    return raw


def _check_budget(raw: Any, key: str) -> int | None:
    """Optional advisory chat limit; 0 means unlimited, absent means default."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise WorkflowMutationError(f"{key} must be a non-negative integer")
    if raw < 0:
        raise WorkflowMutationError(f"{key} must be a non-negative integer")
    return raw


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
    if "modes" in body:
        updates["modes"] = _check_ci_modes(body["modes"])
    if not updates:
        raise WorkflowMutationError(
            "body must set enabled, interval_ms, max_turns, modes, and/or agent_kind"
        )
    return updates


def _check_ci_modes(raw: Any) -> list[str]:
    """Improvement modes; `[]` clears back to readiness-only."""
    if raw is None:
        return []
    try:
        return list(validated_ci_modes(raw))
    except ConfigValidationError as exc:
        raise WorkflowMutationError(exc.message) from exc


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
                    "agent_kinds": sorted(SUPPORTED_AGENT_KINDS),
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
            "agent_kind": _check_agent_kind(body.get("agent_kind")) or None,
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
            fields["agent_kind"] = _check_agent_kind(body.get("agent_kind"))
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
        raw_target = body.get(
            "fix_state", body.get("rca_state", body.get("target_state"))
        )
        if raw_target is not None and not isinstance(raw_target, str):
            raise WorkflowMutationError("fix_state must be a string")
        agent_kind = (
            _check_agent_kind(body.get("agent_kind")) if "agent_kind" in body else None
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
        running_states = {i.state.lower() for i in orchestrator.iter_running_issues()}
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
            for issue in await asyncio.to_thread(tracker.fetch_issues_by_states, [old]):
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

    async def handle_lane_presets_get(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        return web.json_response(
            {
                "presets": [
                    {
                        "name": preset.name,
                        "label": preset.label,
                        "active_states": list(preset.active_states),
                        "terminal_states": list(preset.terminal_states),
                    }
                    for preset in LANE_PRESETS.values()
                ],
                "current": guess_lane_preset(cfg.tracker.active_states),
            }
        )

    async def handle_lane_preset_apply(request: web.Request) -> web.Response:
        body = await _read_json(request)
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowMutationError("body must contain string `name`")
        try:
            preset = get_lane_preset(name)
        except ValueError as exc:
            raise WorkflowMutationError(str(exc)) from exc
        cfg = ctx.config()
        tracker = ctx.file_tracker()
        # Same guard as handle_states_put: a running worker owns its
        # ticket's state string — refuse to pull a lane out from under it.
        preset_active = {s.lower() for s in preset.active_states}
        for issue in orchestrator.iter_running_issues():
            if issue.state.lower() not in preset_active:
                return _json_error(
                    409,
                    "state_in_use",
                    f"column {issue.state!r} has a running worker; wait or pause first",
                )

        plan = await asyncio.to_thread(apply_lane_preset, cfg.workflow_path, name)
        migrated: dict[str, str] = {}
        skipped: list[str] = []
        for old in plan.removed:
            for issue in await asyncio.to_thread(tracker.fetch_issues_by_states, [old]):
                if orchestrator.find_running_issue_id(issue.identifier) is not None:
                    skipped.append(issue.identifier)
                    continue
                await asyncio.to_thread(
                    tracker.transition, issue.identifier, plan.fallback_state
                )
                migrated[issue.identifier] = plan.fallback_state
        orchestrator.workflow_state.reload()
        orchestrator.request_refresh()
        # F-23: an 8-lane preset needs `agent.max_turns >= len(active_states)`
        # or the very next dispatch fails preflight. Report it in the response
        # instead of letting the operator discover it at run time.
        reloaded = orchestrator.workflow_state.current()
        warning = stage_turn_budget_error(reloaded) if reloaded is not None else None
        if warning:
            log.warning(
                "lane_preset_turn_budget_warning", preset=preset.name, detail=warning
            )
        return web.json_response(
            {
                "applied": preset.name,
                "added": plan.added,
                "removed": plan.removed,
                "migrated": migrated,
                "skipped_running": skipped,
                "fallback_state": plan.fallback_state,
                "warning": warning,
            }
        )

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
            modes=updates.get("modes"),
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

    app.router.add_get("/api/v1/workflow", _wrap(handle_workflow_get))
    app.router.add_put("/api/v1/workflow/states", _wrap(handle_states_put))
    app.router.add_get("/api/v1/workflow/prompts/{state}", _wrap(handle_prompt_get))
    app.router.add_put("/api/v1/workflow/prompts/{state}", _wrap(handle_prompt_put))
    app.router.add_put(
        "/api/v1/workflow/branch-policy", _wrap(handle_branch_policy_put)
    )
    app.router.add_get("/api/v1/workflow/presets", _wrap(handle_lane_presets_get))
    app.router.add_post(
        "/api/v1/workflow/presets/apply", _wrap(handle_lane_preset_apply)
    )
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


# ---------------------------------------------------------------------------
# routes: git (host-repo history, task branches, manual merge)
# ---------------------------------------------------------------------------


def _register_git_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    def _effective_target(cfg: ServiceConfig, workflow_dir: Path) -> str | None:
        return cfg.agent.auto_merge_target_branch or git_inspect.current_branch(
            workflow_dir
        )

    async def handle_git_branches(_request: web.Request) -> web.Response:
        branches = await asyncio.to_thread(
            git_inspect.list_branches, ctx.workflow_dir()
        )
        return web.json_response({"branches": branches})

    async def handle_git_log(request: web.Request) -> web.Response:
        raw_branch = (request.query.get("branch") or "").strip()
        branch = _check_branch(raw_branch) if raw_branch else ""
        raw_limit = (request.query.get("limit") or "").strip()
        limit = git_inspect.DEFAULT_LOG_LIMIT
        if raw_limit:
            try:
                limit = int(raw_limit)
            except ValueError:
                return _json_error(400, "invalid_limit", "limit must be an integer")
        workflow_dir = ctx.workflow_dir()

        def _load() -> tuple[str | None, list[dict[str, object]] | None]:
            if not git_inspect.is_git_repo(workflow_dir):
                return "not_a_git_repo", []
            if branch and not git_inspect.ref_exists(workflow_dir, branch):
                return None, None
            return None, git_inspect.commit_log(
                workflow_dir, ref=branch or None, limit=limit
            )

        note, commits = await asyncio.to_thread(_load)
        if commits is None:
            return _json_error(400, "unknown_ref", f"unknown ref {branch!r}")
        return web.json_response(
            {"branch": branch or None, "commits": commits, "note": note}
        )

    async def handle_git_task_branches(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent

        def _load() -> tuple[str | None, list[dict[str, object]], str | None]:
            if not git_inspect.is_git_repo(workflow_dir):
                return None, [], "not_a_git_repo"
            target = _effective_target(cfg, workflow_dir)
            return target, git_inspect.list_task_branches(workflow_dir, target), None

        target, rows, note = await asyncio.to_thread(_load)
        tickets: dict[str, Issue] = {}
        if rows and cfg.tracker.kind == "file":
            try:
                issues = await asyncio.to_thread(
                    ctx.file_tracker().fetch_issues_by_states,
                    [*cfg.tracker.active_states, *cfg.tracker.terminal_states],
                )
                tickets = {i.identifier: i for i in issues}
            except Exception as exc:
                # Ticket enrichment is best-effort; the git view stays useful.
                log.warning("git_task_branches_ticket_lookup_failed", error=str(exc))
        for row in rows:
            identifier = str(row["identifier"])
            issue = tickets.get(identifier)
            row["ticket"] = (
                {
                    "identifier": issue.identifier,
                    "title": issue.title,
                    "state": issue.state,
                }
                if issue is not None
                else None
            )
            row["running"] = orchestrator.find_running_issue_id(identifier) is not None
        return web.json_response(
            {
                "target_branch": target,
                "auto_merge_enabled": cfg.agent.auto_merge_on_done,
                "auto_merge_push_target": cfg.agent.auto_merge_push_target,
                "merge_delivery": (
                    "upstream-publishing"
                    if cfg.agent.auto_merge_push_target
                    else "local-only"
                ),
                "branches": rows,
                "note": note,
            }
        )

    async def handle_git_compare(request: web.Request) -> web.Response:
        branch = _check_branch(request.query.get("branch"))
        raw_target = (request.query.get("target") or "").strip()
        target = _check_branch(raw_target, key="target") if raw_target else None
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent

        def _load() -> tuple[str, str, None] | tuple[None, None, dict[str, object]]:
            if not git_inspect.is_git_repo(workflow_dir):
                return "not_a_git_repo", "workflow dir is not a git repository", None
            resolved = target or _effective_target(cfg, workflow_dir)
            if not resolved:
                return (
                    "no_target",
                    "no target branch; set auto_merge_target_branch or pass target",
                    None,
                )
            for ref in (branch, resolved):
                if not git_inspect.ref_exists(workflow_dir, ref):
                    return "unknown_ref", f"unknown ref {ref!r}", None
            return None, None, git_inspect.compare_refs(workflow_dir, branch, resolved)

        code, message, payload = await asyncio.to_thread(_load)
        if payload is None:
            assert code is not None and message is not None
            return _json_error(400, code, message)
        return web.json_response(payload)

    async def handle_git_diff(request: web.Request) -> web.Response:
        commit = (request.query.get("commit") or "").strip()
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent

        if commit:
            if not _COMMIT_RE.match(commit):
                raise WorkflowMutationError(f"invalid commit {commit!r}")

            def _load_commit() -> dict[str, object] | None:
                if not git_inspect.is_git_repo(workflow_dir):
                    return None
                if not git_inspect.ref_exists(workflow_dir, commit):
                    return None
                return git_inspect.commit_patch(workflow_dir, commit)

            payload = await asyncio.to_thread(_load_commit)
            if payload is None:
                return _json_error(400, "unknown_ref", f"unknown commit {commit!r}")
            return web.json_response({"commit": commit, **payload})

        branch = _check_branch(request.query.get("branch"))
        raw_target = (request.query.get("target") or "").strip()
        target = _check_branch(raw_target, key="target") if raw_target else None
        path = (request.query.get("path") or "").strip() or None
        if path is not None and (path.startswith("-") or "\x00" in path):
            raise WorkflowMutationError(f"invalid path {path!r}")

        def _load() -> tuple[str, str, None] | tuple[None, str, dict[str, object]]:
            if not git_inspect.is_git_repo(workflow_dir):
                return "not_a_git_repo", "workflow dir is not a git repository", None
            resolved = target or _effective_target(cfg, workflow_dir)
            if not resolved:
                return (
                    "no_target",
                    "no target branch; set auto_merge_target_branch or pass target",
                    None,
                )
            for ref in (branch, resolved):
                if not git_inspect.ref_exists(workflow_dir, ref):
                    return "unknown_ref", f"unknown ref {ref!r}", None
            return (
                None,
                resolved,
                git_inspect.diff_patch(workflow_dir, branch, resolved, path),
            )

        code, detail, payload = await asyncio.to_thread(_load)
        if payload is None:
            assert code is not None
            return _json_error(400, code, detail)
        return web.json_response({"branch": branch, "target": detail, **payload})

    merge_lock = asyncio.Lock()

    async def handle_git_merge(request: web.Request) -> web.Response:
        body = await _read_json(request)
        branch = _check_branch(body.get("branch"))
        if not branch.startswith(SYMPHONY_BRANCH_PREFIX):
            raise WorkflowMutationError(
                f"merge is limited to {SYMPHONY_BRANCH_PREFIX}* task branches"
            )
        identifier = _check_identifier(branch[len(SYMPHONY_BRANCH_PREFIX) :])
        raw_target = body.get("target")
        target = (
            _check_branch(raw_target, key="target")
            if raw_target not in (None, "")
            else None
        )
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent
        if orchestrator.find_running_issue_id(identifier) is not None:
            return _json_error(
                409,
                "state_in_use",
                f"{identifier} has a running worker; pause or wait before merging",
            )
        if merge_lock.locked():
            return _json_error(
                409, "merge_in_progress", "another merge is already running"
            )
        async with merge_lock:
            resolved_target = target or await asyncio.to_thread(
                _effective_target, cfg, workflow_dir
            )
            if not resolved_target:
                return _json_error(
                    400,
                    "no_target",
                    "no target branch; set auto_merge_target_branch or pass target",
                )
            issue: Issue | None = None
            if cfg.tracker.kind == "file":
                try:
                    issue = await asyncio.to_thread(
                        ctx.file_tracker().fetch_issue_full_by_id, identifier
                    )
                except Exception as exc:
                    log.warning(
                        "git_merge_ticket_lookup_failed",
                        identifier=identifier,
                        error=str(exc),
                    )
            # Same engine and policy as the automatic Verify merge gate:
            # exclude_paths, --no-ff, conflict preflight, dirty-host guard.
            result = await auto_merge_on_done_best_effort(
                workflow_dir=workflow_dir,
                branch=branch,
                identifier=identifier,
                title=issue.title if issue is not None else identifier,
                target_branch=resolved_target,
                exclude_paths=cfg.agent.auto_merge_exclude_paths,
                capture_untracked=cfg.agent.auto_merge_capture_untracked,
                push_target=cfg.agent.auto_merge_push_target,
            )
        if not result.ok:
            return _json_error(
                409, f"merge_{result.status}", result.detail or result.status
            )
        note_appended = False
        if issue is not None:
            note_body = (
                f"Operator merged `{branch}` into `{resolved_target}` from "
                "the web UI's Git page.\n\n"
                f"- status: `{result.status}`"
            )
            if result.detail.strip():
                note_body = f"{note_body}\n- detail: {result.detail.strip()[:1000]}"
            try:
                await asyncio.to_thread(
                    ctx.file_tracker().append_note, issue, "Manual Merge", note_body
                )
                note_appended = True
            except Exception as exc:
                log.warning(
                    "git_merge_note_append_failed",
                    identifier=identifier,
                    error=str(exc),
                )
        orchestrator.request_refresh()
        return web.json_response(
            {
                "ok": True,
                "status": result.status,
                "detail": result.detail,
                "branch": branch,
                "target": resolved_target,
                "ticket_note_appended": note_appended,
            }
        )

    # ---- mutating branch actions ------------------------------------
    # Scope: task branches always; the merge target only for `push`, and
    # only when the operator retypes its name. Nothing here ever forces a
    # push, and a delete only uses `-D` after an explicit force or a
    # verified merge into the target.

    def _task_branch_identifier(branch: str) -> str:
        if not branch.startswith(SYMPHONY_BRANCH_PREFIX):
            raise WorkflowMutationError(
                f"limited to {SYMPHONY_BRANCH_PREFIX}* task branches"
            )
        return _check_identifier(branch[len(SYMPHONY_BRANCH_PREFIX) :])

    def _resolve_remote(workflow_dir: Path, raw: Any) -> str | None:
        if raw in (None, ""):
            return git_ops.default_remote(workflow_dir)
        if not isinstance(raw, str) or not git_ops.is_valid_remote_name(raw.strip()):
            raise WorkflowMutationError(f"invalid remote name {raw!r}")
        remote = raw.strip()
        return remote if remote in git_ops.list_remotes(workflow_dir) else None

    async def handle_git_remote_status(_request: web.Request) -> web.Response:
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent

        def _load() -> dict[str, object]:
            if not git_inspect.is_git_repo(workflow_dir):
                return {"remotes": [], "note": "not_a_git_repo"}
            return {
                "remotes": git_ops.list_remotes(workflow_dir),
                "default_remote": git_ops.default_remote(workflow_dir),
                "current_branch": git_inspect.current_branch(workflow_dir),
                "target_branch": _effective_target(cfg, workflow_dir),
                "note": None,
            }

        payload = await asyncio.to_thread(_load)
        # `gh` presence is a property of the host, not of the repo.
        payload["gh_available"] = git_ops.gh_available()
        return web.json_response(payload)

    async def handle_git_branch_delete(request: web.Request) -> web.Response:
        body = await _read_json(request)
        branch = _check_branch(body.get("branch"))
        identifier = _task_branch_identifier(branch)
        force = bool(body.get("force"))
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent
        if orchestrator.find_running_issue_id(identifier) is not None:
            return _json_error(
                409,
                "state_in_use",
                f"{identifier} has a running worker; pause or wait before deleting",
            )

        def _delete() -> tuple[str, str] | GitOpResult:
            if not git_inspect.is_git_repo(workflow_dir):
                return "not_a_git_repo", "workflow dir is not a git repository"
            if not git_inspect.ref_exists(workflow_dir, branch):
                return "unknown_ref", f"unknown branch {branch!r}"
            if git_inspect.current_branch(workflow_dir) == branch:
                return "checked_out", f"{branch} is checked out; switch away first"
            target = _effective_target(cfg, workflow_dir)
            merged = bool(target) and git_inspect.is_merged(
                workflow_dir, branch, target
            )
            if not merged and not force:
                return (
                    "not_merged",
                    f"{branch} is not merged into {target or 'the target branch'}; "
                    "merge it first or repeat with force",
                )
            # `-D` only once the merge is proven or the operator forced it;
            # plain `-d` can still refuse a branch merged into the target
            # but not into HEAD.
            return git_ops.delete_branch(workflow_dir, branch, force=True)

        outcome = await asyncio.to_thread(_delete)
        if isinstance(outcome, tuple):
            code, message = outcome
            return _json_error(409 if code != "unknown_ref" else 400, code, message)
        if not outcome.ok:
            return _json_error(409, outcome.status, outcome.detail)
        orchestrator.request_refresh()
        return web.json_response({**outcome.as_dict(), "branch": branch})

    async def handle_git_push(request: web.Request) -> web.Response:
        body = await _read_json(request)
        branch = _check_branch(body.get("branch"))
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent
        target = await asyncio.to_thread(_effective_target, cfg, workflow_dir)
        is_task_branch = branch.startswith(SYMPHONY_BRANCH_PREFIX)
        if not is_task_branch:
            if not target or branch != target:
                return _json_error(
                    400,
                    "branch_not_pushable",
                    f"push is limited to {SYMPHONY_BRANCH_PREFIX}* task branches "
                    "and the merge target branch",
                )
            # Pushing the shared target moves work other people pull, so it
            # takes a deliberate second act, not one click.
            if body.get("confirm") != branch:
                return _json_error(
                    400,
                    "confirm_required",
                    f"pushing {branch} requires confirm: {branch!r}",
                )
        remote = await asyncio.to_thread(
            _resolve_remote, workflow_dir, body.get("remote")
        )
        if not remote:
            return _json_error(
                400, "no_remote", "this repository has no matching git remote"
            )
        if not await asyncio.to_thread(git_inspect.ref_exists, workflow_dir, branch):
            return _json_error(400, "unknown_ref", f"unknown branch {branch!r}")
        result = await asyncio.to_thread(
            git_ops.push_branch, workflow_dir, branch, remote
        )
        if not result.ok:
            return _json_error(409, result.status, result.detail)
        return web.json_response(
            {**result.as_dict(), "branch": branch, "remote": remote}
        )

    async def handle_git_pull_request(request: web.Request) -> web.Response:
        body = await _read_json(request)
        branch = _check_branch(body.get("branch"))
        identifier = _task_branch_identifier(branch)
        raw_target = body.get("target")
        requested_target = (
            _check_branch(raw_target, key="target")
            if raw_target not in (None, "")
            else None
        )
        cfg = ctx.config()
        workflow_dir = cfg.workflow_path.parent
        target = requested_target or await asyncio.to_thread(
            _effective_target, cfg, workflow_dir
        )
        if not target:
            return _json_error(
                400,
                "no_target",
                "no target branch; set auto_merge_target_branch or pass target",
            )
        if not git_ops.gh_available():
            return _json_error(
                400,
                "gh_unavailable",
                "the GitHub CLI (gh) is not installed or not on PATH",
            )
        remote = await asyncio.to_thread(
            _resolve_remote, workflow_dir, body.get("remote")
        )
        if not remote:
            return _json_error(
                400, "no_remote", "this repository has no matching git remote"
            )
        if not await asyncio.to_thread(
            git_ops.branch_on_remote, workflow_dir, remote, branch
        ):
            return _json_error(
                409,
                "branch_not_pushed",
                f"{branch} is not on {remote} yet; push it before opening a PR",
            )
        issue: Issue | None = None
        if cfg.tracker.kind == "file":
            try:
                issue = await asyncio.to_thread(
                    ctx.file_tracker().fetch_issue_full_by_id, identifier
                )
            except Exception as exc:
                log.warning(
                    "git_pr_ticket_lookup_failed", identifier=identifier, error=str(exc)
                )
        raw_title = body.get("title")
        title = (
            _check_title(raw_title)
            if raw_title not in (None, "")
            else f"{identifier}: {issue.title}"
            if issue is not None
            else identifier
        )
        raw_body = body.get("body")
        pr_body = (
            _check_description(raw_body)
            if raw_body not in (None, "")
            else f"Symphony task branch for `{identifier}`."
        )
        result = await asyncio.to_thread(
            git_ops.create_pull_request, workflow_dir, branch, target, title, pr_body
        )
        if not result.ok:
            return _json_error(409, result.status, result.detail)
        return web.json_response(
            {**result.as_dict(), "branch": branch, "target": target}
        )

    app.router.add_get("/api/v1/git/branches", _wrap(handle_git_branches))
    app.router.add_get("/api/v1/git/remote-status", _wrap(handle_git_remote_status))
    app.router.add_post("/api/v1/git/branch/delete", _wrap(handle_git_branch_delete))
    app.router.add_post("/api/v1/git/push", _wrap(handle_git_push))
    app.router.add_post("/api/v1/git/pr", _wrap(handle_git_pull_request))
    app.router.add_get("/api/v1/git/log", _wrap(handle_git_log))
    app.router.add_get("/api/v1/git/task-branches", _wrap(handle_git_task_branches))
    app.router.add_get("/api/v1/git/compare", _wrap(handle_git_compare))
    app.router.add_get("/api/v1/git/diff", _wrap(handle_git_diff))
    app.router.add_post("/api/v1/git/merge", _wrap(handle_git_merge))


# ---------------------------------------------------------------------------
# routes: operator chat (REST mutations + one-way WebSocket stream)
# ---------------------------------------------------------------------------


def _register_chat_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    # request_refresh lets board tickets the chat agent files in edit mode
    # dispatch on the next tick instead of waiting out the poll interval.
    project_registry = ProjectRegistry()
    manager = ChatManager(
        ctx.config,
        request_refresh=orchestrator.request_refresh,
        project_creator=lambda name, path, *, expected_target: (
            _create_or_adopt_registered_project(
                project_registry, name=name, path=path, expected_target=expected_target
            )
        ),
    )
    app[CHAT_MANAGER_KEY] = manager
    websockets: set[web.WebSocketResponse] = set()

    # The singular `/chat/session` routes predate multi-session support and
    # stay as an alias for "the active session"; everything below shares one
    # implementation so the two shapes cannot drift.

    async def _start(body: dict[str, Any]) -> web.Response:
        mode = body.get("mode", "qa")
        if not isinstance(mode, str):
            raise WorkflowMutationError("mode must be a string")
        agent_kind = None
        if body.get("agent_kind") not in (None, ""):
            agent_kind = _check_agent_kind(body.get("agent_kind"))
        max_turns = _check_budget(body.get("max_turns"), "max_turns")
        max_tokens = _check_budget(body.get("max_tokens"), "max_tokens")
        confirmation_token = _check_chat_confirmation_token(
            body.get("confirmation_token")
        )
        try:
            snapshot = await manager.start_session(
                mode,
                agent_kind,
                max_turns=max_turns,
                max_tokens=max_tokens,
                confirmation_token=confirmation_token,
            )
        except ChatSessionExistsError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot, status=201)

    async def _set_mode(body: dict[str, Any], session_id: str | None) -> web.Response:
        mode = body.get("mode")
        if not isinstance(mode, str):
            raise WorkflowMutationError("mode must be a string")
        try:
            result = await manager.set_mode(mode, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatBusyError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(result)

    async def _stop(session_id: str | None, forget: bool = False) -> web.Response:
        try:
            await manager.stop_session(session_id, forget=forget)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        return web.json_response({"stopped": True, "forgotten": forget})

    async def _confirm_project_setup(
        request: web.Request, session_id: str | None, action_id: str
    ) -> web.Response:
        # This crosses from chat into global registry/Git mutation. Preserve the
        # same loopback + same-origin boundary as the Project management form.
        denied = _project_mutation_error(request)
        if denied is not None:
            return denied
        try:
            action = await manager.confirm_project_setup(
                action_id,
                session_id,
                confirmation_token=request.headers.get("X-Symphony-Chat-Confirmation"),
            )
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatProjectAuthorizationError as exc:
            return _json_error(403, exc.code, exc.message)
        except ChatProjectActionError as exc:
            status = (
                404 if exc.message.startswith("unknown project setup action") else 409
            )
            return _json_error(status, exc.code, exc.message)
        if action["status"] == "failed":
            return web.json_response(
                {
                    "error": {
                        "code": "project_setup_failed",
                        "message": str(action.get("error") or "project setup failed"),
                    },
                    "action": action,
                },
                status=409,
            )
        return web.json_response({"action": action})

    async def _send(
        request: web.Request, body: dict[str, Any], session_id: str | None
    ) -> web.Response:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WorkflowMutationError("text is required")
        if len(text) > _MAX_CHAT_MESSAGE:
            raise WorkflowMutationError(
                f"text too long (max {_MAX_CHAT_MESSAGE} chars)"
            )
        try:
            selected = manager.project_setup_for_choice(text, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        # A bare numeric response is an action only when it matches a live,
        # server-issued choice. All other text remains ordinary conversation.
        if selected is not None:
            return await _confirm_project_setup(request, session_id, selected.action_id)
        try:
            snapshot = await manager.send_message(text, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatBackendUnavailableError as exc:
            return _json_error(409, exc.code, exc.message)
        except ChatBusyError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot, status=202)

    async def handle_chat_session_get(_request: web.Request) -> web.Response:
        return web.json_response(manager.snapshot())

    async def handle_chat_session_post(request: web.Request) -> web.Response:
        body = await _read_json(request)
        if manager.live_count:
            return _json_error(
                409,
                "chat_session_exists",
                "a chat session is already active; stop it first or use "
                "/api/v1/chat/sessions",
            )
        return await _start(body)

    async def handle_chat_session_patch(request: web.Request) -> web.Response:
        return await _set_mode(await _read_json(request), None)

    async def handle_chat_session_delete(_request: web.Request) -> web.Response:
        return await _stop(None)

    async def handle_chat_message_post(request: web.Request) -> web.Response:
        return await _send(request, await _read_json(request), None)

    async def handle_chat_sessions_get(_request: web.Request) -> web.Response:
        return web.json_response(manager.list_sessions())

    async def handle_chat_sessions_post(request: web.Request) -> web.Response:
        return await _start(await _read_json(request))

    async def handle_chat_session_detail(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        snapshot = manager.snapshot(session_id)
        if not snapshot.get("active"):
            return _json_error(
                404, "chat_no_session", f"no live chat session {session_id!r}"
            )
        return web.json_response(snapshot)

    async def handle_chat_session_id_patch(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        return await _set_mode(await _read_json(request), session_id)

    async def handle_chat_session_id_delete(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        forget = request.query.get("forget", "").lower() in {"1", "true", "yes"}
        return await _stop(session_id, forget=forget)

    async def handle_chat_session_id_message(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        return await _send(request, await _read_json(request), session_id)

    async def handle_chat_project_setup_select(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        action_id = _check_project_setup_action_id(request.match_info["action_id"])
        body = await _read_json(request)
        if body:
            return _json_error(
                400, "invalid_body", "project setup selection takes no fields"
            )
        return await _confirm_project_setup(request, session_id, action_id)

    async def handle_chat_session_reattach(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        body = await _read_json(request)
        if set(body) - {"confirmation_token"}:
            raise WorkflowMutationError("reattach accepts only confirmation_token")
        token = _check_chat_confirmation_token(body.get("confirmation_token"))
        try:
            snapshot = await manager.reattach(session_id, confirmation_token=token)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatSessionExistsError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot)

    def _origin_allowed(request: web.Request) -> bool:
        # Browsers do not apply CORS to WebSocket upgrades; without this an
        # arbitrary web page could stream the operator's transcript.
        return _origin_is_trusted(request, request.headers.get("Origin") or "")

    async def _pump(
        queue: asyncio.Queue[dict[str, Any] | None], ws: web.WebSocketResponse
    ) -> None:
        while True:
            row = await queue.get()
            try:
                if row is None:
                    await ws.close(code=WSCloseCode.GOING_AWAY, message=b"shutdown")
                    return
                await ws.send_json(row)
            except (ConnectionResetError, RuntimeError):
                return

    async def handle_chat_ws(request: web.Request) -> web.StreamResponse:
        bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
        if bind in _LOOPBACK_BINDS and not _origin_allowed(request):
            return _json_error(
                403, "forbidden_origin", "cross-origin websocket rejected"
            )
        raw_focus = (request.query.get("session") or "").strip()
        focus = _check_chat_session_id(raw_focus) if raw_focus else None
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        websockets.add(ws)
        queue = manager.subscribe(focus)
        pump = asyncio.create_task(_pump(queue, ws))
        try:
            await ws.send_json(
                {
                    "type": "hello",
                    "snapshot": manager.snapshot(focus),
                    "sessions": manager.list_sessions(),
                }
            )
            # Server->client stream, except for one client message: which
            # session's token deltas this socket wants. Draining also
            # detects disconnect.
            async for message in ws:
                if message.type is not WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(message.data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(frame, dict) or frame.get("type") != "focus":
                    continue
                requested = frame.get("session_id")
                if requested in (None, ""):
                    manager.set_focus(queue, None)
                    continue
                try:
                    manager.set_focus(queue, _check_chat_session_id(requested))
                except WorkflowMutationError:
                    continue
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):
                pass
            manager.unsubscribe(queue)
            websockets.discard(ws)
        return ws

    async def _close_chat(_app: web.Application) -> None:
        # Close sockets first: an open WS otherwise holds runner.cleanup()
        # until shutdown_timeout expires.
        for ws in list(websockets):
            try:
                await ws.close(code=WSCloseCode.GOING_AWAY, message=b"shutdown")
            except Exception:
                pass
        websockets.clear()
        await manager.close()

    app.on_shutdown.append(_close_chat)

    app.router.add_get("/api/v1/chat/session", _wrap(handle_chat_session_get))
    app.router.add_post("/api/v1/chat/session", _wrap(handle_chat_session_post))
    app.router.add_patch("/api/v1/chat/session", _wrap(handle_chat_session_patch))
    app.router.add_delete("/api/v1/chat/session", _wrap(handle_chat_session_delete))
    app.router.add_post("/api/v1/chat/message", _wrap(handle_chat_message_post))
    app.router.add_get("/api/v1/chat/sessions", _wrap(handle_chat_sessions_get))
    app.router.add_post("/api/v1/chat/sessions", _wrap(handle_chat_sessions_post))
    app.router.add_get(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_detail)
    )
    app.router.add_patch(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_id_patch)
    )
    app.router.add_delete(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_id_delete)
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/message",
        _wrap(handle_chat_session_id_message),
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/project-setup/{action_id}/select",
        _wrap(handle_chat_project_setup_select),
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/reattach",
        _wrap(handle_chat_session_reattach),
    )
    app.router.add_get("/api/v1/chat/ws", handle_chat_ws)


# ---------------------------------------------------------------------------
# routes: stats + static SPA
# ---------------------------------------------------------------------------


def _register_preview_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    manager = ProductPreviewManager()

    def release_gate(
        cfg: ServiceConfig, *, authoritative: bool = False
    ) -> dict[str, Any]:
        ticket = cfg.preview.release_ticket
        if not ticket:
            return {
                "ticket": "",
                "state": None,
                "ready": True,
                "reason": "No release ticket configured",
            }
        state: str | None = None
        title: str | None = None
        if cfg.tracker.kind == "file":
            issue = ctx.file_tracker().fetch_issue_full_by_id(ticket)
            if issue is not None:
                state, title = issue.state, issue.title
        elif authoritative:
            # Launch/restart must use tracker truth, not a possibly stale live
            # orchestrator snapshot. Status polling keeps the cheap snapshot.
            with tracker_context_manager(cfg) as tracker:
                issue = tracker.fetch_issue_full_by_id(ticket)
            if issue is not None:
                state, title = issue.state, issue.title
        else:
            snapshot = orchestrator.issue_snapshot(ticket)
            if snapshot:
                state = snapshot.get("state")
                title = snapshot.get("title")
        ready = isinstance(state, str) and state.strip().lower() == "done"
        reason = (
            "Release verification passed"
            if ready
            else f"{ticket} must be Done before launch"
        )
        return {
            "ticket": ticket,
            "title": title,
            "state": state,
            "ready": ready,
            "reason": reason,
        }

    async def payload() -> dict[str, Any]:
        cfg = ctx.config()
        status = await manager.status(cfg)
        if not cfg.preview.enabled and status["phase"] == "stopped":
            status["phase"] = "disabled"
        return {
            "configured": bool(cfg.preview.command),
            "enabled": cfg.preview.enabled,
            **status,
            "release_gate": release_gate(cfg),
            "acceptance": list(cfg.preview.acceptance),
        }

    async def get_preview(_request: web.Request) -> web.Response:
        return web.json_response(await payload())

    async def mutate(request: web.Request, action: str) -> web.Response:
        # Unlike ordinary JSON mutations, these bodyless process controls
        # would otherwise be submit-able by a cross-origin HTML form without
        # a CORS preflight. Require JSON even when the body is empty.
        if request.content_type != "application/json":
            return _json_error(
                415,
                "unsupported_media_type",
                "Product Preview actions require application/json",
            )
        bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
        if bind not in _LOOPBACK_BINDS:
            return _json_error(
                403,
                "preview_loopback_required",
                "Product Preview process control is available only on loopback",
            )
        body = await _read_json(request)
        if body:
            return _json_error(
                400,
                "invalid_body",
                "Product Preview actions do not accept command or path input",
            )
        cfg = ctx.config()
        gate = release_gate(cfg, authoritative=action in {"start", "restart"})
        if action in {"start", "restart"} and not gate["ready"]:
            return _json_error(409, "release_not_ready", str(gate["reason"]))
        try:
            if action == "start":
                await manager.start(cfg)
            elif action == "restart":
                await manager.restart(cfg)
            else:
                await manager.stop(cfg)
        except ProductPreviewError as exc:
            return _json_error(409, "preview_error", str(exc))
        return web.json_response(await payload())

    app.router.add_get("/api/v1/preview", _wrap(get_preview))
    app.router.add_post("/api/v1/preview/start", _wrap(partial(mutate, action="start")))
    app.router.add_post(
        "/api/v1/preview/restart", _wrap(partial(mutate, action="restart"))
    )
    app.router.add_post("/api/v1/preview/stop", _wrap(partial(mutate, action="stop")))

    async def cleanup(_app: web.Application) -> None:
        await manager.close()

    app.on_cleanup.append(cleanup)


def _status_is_running(status: Any) -> bool:
    if isinstance(status, bool):
        return status
    if isinstance(status, dict):
        if "running" in status:
            return bool(status["running"])
        return str(status.get("state", "")).lower() == "running"
    return str(getattr(status, "state", "")).lower() == "running"


def _project_url(project: Project, status: Any | None = None) -> str:
    record = getattr(status, "record", None) if status is not None else None
    host = str(getattr(record, "host", None) or project.host)
    port = int(getattr(record, "port", None) or project.port)
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _create_or_adopt_registered_project(
    registry: ProjectRegistry,
    *,
    name: str,
    path: Path,
    expected_target: ProjectTargetExpectation | None = None,
) -> Project:
    """Keep the web boundary thin around the shared project setup service."""
    from .projects import create_or_adopt_project, source_checkout

    return create_or_adopt_project(
        path,
        source=source_checkout(),
        registry=registry,
        name=name,
        expected_target=expected_target,
    )


def _current_project_payload(ctx: _Ctx, projects: list[Project]) -> dict[str, Any]:
    cfg = ctx.config()
    workflow = cfg.workflow_path.expanduser().resolve()
    current = next(
        (
            project
            for project in projects
            if Path(project.workflow).expanduser().resolve() == workflow
        ),
        None,
    )
    repo = (
        Path(current.git_repo).expanduser().resolve()
        if current is not None
        else canonical_project_repo(workflow.parent)
    )
    board = cfg.tracker.board_root
    return {
        "id": current.id if current is not None else None,
        "name": current.name if current is not None else repo.name,
        "repo_path": str(repo),
        "workflow_path": str(workflow),
        "board_path": str(board.expanduser().resolve()) if board is not None else None,
        "registered": current is not None,
    }


def _project_mutation_error(request: web.Request) -> web.Response | None:
    """Project setup can write arbitrary paths, so it is loopback-only."""
    bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
    if bind not in _LOOPBACK_BINDS:
        return _json_error(
            403, "project_mutation_forbidden", "project management is loopback-only"
        )
    remote = request.remote
    try:
        if remote is None or not ipaddress.ip_address(remote).is_loopback:
            return _json_error(
                403, "project_mutation_forbidden", "project management is loopback-only"
            )
    except ValueError:
        return _json_error(
            403, "project_mutation_forbidden", "project management is loopback-only"
        )
    origin = request.headers.get("Origin")
    if not _origin_is_trusted(request, origin or ""):
        return _json_error(
            403,
            "forbidden_origin",
            f"origin {origin!r} may not manage projects; set "
            f"{TRUSTED_ORIGINS_ENV}={origin} if you front this board with a "
            "reverse proxy or tunnel",
        )
    if request.content_length is not None and request.content_length > 16_384:
        return _json_error(413, "request_too_large", "project request is too large")
    return None


def _register_project_routes(app: web.Application, ctx: _Ctx) -> None:
    registry = ProjectRegistry()

    async def handle_projects(_request: web.Request) -> web.Response:
        try:
            projects = await asyncio.to_thread(registry.list)
            statuses = await asyncio.gather(
                *(
                    asyncio.to_thread(registry.status, project.id)
                    for project in projects
                ),
                return_exceptions=True,
            )
        except ProjectError as exc:
            return _json_error(409, "project_registry_error", str(exc))
        current = await asyncio.to_thread(_current_project_payload, ctx, projects)
        payload = []
        for project, status in zip(projects, statuses, strict=True):
            status_error = isinstance(status, BaseException)
            running = False if status_error else _status_is_running(status)
            payload.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "repo_path": str(Path(project.git_repo).expanduser().resolve()),
                    "workflow_path": str(Path(project.workflow).expanduser().resolve()),
                    "host": project.host,
                    "port": project.port,
                    "running": running,
                    "status_error": "status unavailable" if status_error else None,
                    "current": project.id == current["id"],
                    "url": _project_url(project, None if status_error else status),
                }
            )
        return web.json_response({"projects": payload, "current": current})

    async def handle_current(_request: web.Request) -> web.Response:
        try:
            projects = await asyncio.to_thread(registry.list)
        except ProjectError as exc:
            return _json_error(409, "project_registry_error", str(exc))
        current = await asyncio.to_thread(_current_project_payload, ctx, projects)
        return web.json_response({"current": current})

    async def handle_create(request: web.Request) -> web.Response:
        denied = _project_mutation_error(request)
        if denied is not None:
            return denied
        body = await _read_json(request)
        name = body.get("name")
        raw_path = body.get("path")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            return _json_error(
                400, "invalid_project_name", "name must be 1-100 characters"
            )
        if (
            not isinstance(raw_path, str)
            or not raw_path.strip()
            or len(raw_path) > 4096
        ):
            return _json_error(
                400, "invalid_project_path", "path must be 1-4096 characters"
            )
        try:
            project = await asyncio.to_thread(
                _create_or_adopt_registered_project,
                registry,
                name=name.strip(),
                path=Path(raw_path).expanduser(),
            )
        except ProjectError as exc:
            return _json_error(409, "project_setup_failed", str(exc))
        return web.json_response(
            {
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "repo_path": str(Path(project.git_repo).expanduser().resolve()),
                    "workflow_path": str(Path(project.workflow).expanduser().resolve()),
                    "url": _project_url(project),
                }
            },
            status=201,
        )

    async def handle_open(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return _json_error(
                415, "unsupported_media_type", "mutations require application/json"
            )
        denied = _project_mutation_error(request)
        if denied is not None:
            return denied
        project_id = request.match_info["project_id"]
        try:
            project = await asyncio.to_thread(registry.get, project_id)
            status = await asyncio.to_thread(registry.status, project_id)
            if not _status_is_running(status):
                result = await asyncio.to_thread(registry.start, project_id)
                if isinstance(result, int) and result != 0:
                    raise ProjectError(
                        f"could not start {project.name!r}; run "
                        f"`symphony service status {project.workflow}` and inspect "
                        "the service log, then retry"
                    )
                status = await asyncio.to_thread(registry.status, project_id)
            if not _status_is_running(status):
                raise ProjectError(
                    f"{project.name!r} did not report running; run "
                    f"`symphony service status {project.workflow}` and inspect "
                    "the service log, then retry"
                )
        except ProjectError as exc:
            missing = str(exc).startswith("unknown project ")
            return _json_error(
                404 if missing else 409,
                "project_not_found" if missing else "project_open_failed",
                str(exc),
            )
        except Exception as exc:
            log.warning("project_open_failed", project_id=project_id, error=str(exc))
            return _json_error(409, "project_open_failed", "could not open project")
        return web.json_response(
            {
                "project_id": project.id,
                "running": True,
                "url": _project_url(project, status),
            }
        )

    app.router.add_get("/api/v1/projects", _wrap(handle_projects))
    app.router.add_get("/api/v1/projects/current", _wrap(handle_current))
    app.router.add_post("/api/v1/projects", _wrap(handle_create))
    app.router.add_post("/api/v1/projects/{project_id}/open", _wrap(handle_open))


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
    app[SERVICE_INSTANCE_ID_KEY] = normalize_service_probe_credential(
        getattr(orchestrator, "service_instance_id", None)
    )
    app.middlewares.append(_api_guard)
    _register_issue_routes(app, ctx, orchestrator)
    _register_workflow_routes(app, ctx, orchestrator)
    _register_git_routes(app, ctx, orchestrator)
    _register_chat_routes(app, ctx, orchestrator)
    _register_preview_routes(app, ctx, orchestrator)
    _register_project_routes(app, ctx)
    _register_meta_routes(app, ctx, orchestrator)
