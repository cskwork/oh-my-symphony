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
chat WebSocket accept it. Once trusted origins are declared, the loopback-only
gates (project management, run diagnostics, `/_debug/tasks`) stop trusting
the TCP peer — which is now the proxy — and read the real client from
`X-Forwarded-For` (first entry) or `Forwarded: for=`; the proxy must set one
of them, and anything but a loopback address there is refused. Without
trusted origins those headers are ignored so a direct caller cannot forge
them.

Package layout: `security` (gates, `_api_guard`, `_wrap`), `context` (`_Ctx`),
`payloads` (JSON serialization), `validation` (request-body checks), and one
`*_routes` module per resource. `register_web_routes` below fixes the route
registration order, which aiohttp matching depends on.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .. import component_installed, pm_installed
from ..orchestrator import Orchestrator
from ..service_identity import normalize_service_probe_credential

# Re-exported for the public API surface; tests patch the consumer module
# (e.g. `symphony.webapi.git_routes.auto_merge_on_done_best_effort`).
from ..orchestrator.scheduler import MAX_DEPENDENCY_EDGES, MAX_DEPENDENCY_NODES
from ..projects import ProjectRegistry
from ..trackers.file import FileBoardTracker
from ..utils.auto_merge import auto_merge_on_done_best_effort

# Leaf modules, imported in dependency order.
from .security import (
    STATIC_DIR,
    _BRANCH_RE,
    _COMMIT_RE,
    _CHAT_SESSION_RE,
    _PROJECT_SETUP_ACTION_RE,
    _INTENT_ACTION_RE,
    _CHAT_CONFIRMATION_TOKEN_RE,
    _RUN_ID_RE,
    _MAX_TITLE,
    _MAX_BODY,
    _MAX_LABELS,
    _ALLOWED_HOSTS,
    _LOOPBACK_BINDS,
    TRUSTED_ORIGINS_ENV,
    API_TOKEN_ENV,
    _CHAT_WS_PATH,
    _HEALTH_PATH,
    _PM_API_PREFIX,
    _CI_EDITABLE_KEYS,
    BIND_HOST_KEY,
    SERVICE_INSTANCE_ID_KEY,
    _MAX_CHAT_MESSAGE,
    _json_error,
    _FORWARDED_FOR_RE,
    _forwarded_client,
    _address_is_loopback,
    _request_is_loopback,
    _request_host,
    _bare_host,
    _host_is_loopback,
    _trusted_origins,
    _host_is_declared_trusted,
    _origin_is_trusted,
    _configured_api_token,
    _request_has_valid_bearer,
    _request_has_valid_ws_query_token,
    _request_has_valid_service_instance,
    _BROWSER_PROVENANCE_HEADERS,
    _request_from_browser,
    _api_guard,
    _read_json,
    _wrap,
)
from .context import _Ctx
from .payloads import (
    _issue_card,
    _PUBLIC_SCHEDULE_REASONS,
    _public_schedule_reason,
    _request_group_schedule_payload,
    _json_safe,
    _columns_payload,
    _continuous_improvement_payload,
    _workflow_payload,
    _live_by_identifier,
)
from .validation import (
    _valid_states,
    _INLINE_ARTIFACT_TYPES,
    _ascii_filename,
    _artifact_json,
    _check_identifier,
    _check_branch,
    _check_title,
    _check_description,
    _check_priority,
    _check_labels,
    _check_request,
    _check_request_schedule_key,
    _check_blocked_by,
    _check_agent_kind,
    _check_chat_session_id,
    _check_project_setup_action_id,
    _check_intent_action_id,
    _check_chat_confirmation_token,
    _check_budget,
    _parse_ci_settings,
    _check_ci_modes,
    _check_state,
    _parse_state_specs,
)
from .issue_routes import _register_issue_routes
from .workflow_routes import _register_workflow_routes
from .git_routes import _register_git_routes
from .project_routes import (
    _status_is_running,
    _project_url,
    _create_or_adopt_registered_project,
    _current_project_payload,
    _project_mutation_error,
    _register_project_routes,
)
from .preview_routes import _register_preview_routes
from .meta_routes import _register_meta_routes


def register_web_routes(app: web.Application, orchestrator: Orchestrator) -> None:
    ctx = _Ctx(orchestrator)
    app[SERVICE_INSTANCE_ID_KEY] = normalize_service_probe_credential(
        getattr(orchestrator, "service_instance_id", None)
    )
    app.middlewares.append(_api_guard)
    _register_issue_routes(app, ctx, orchestrator)
    _register_workflow_routes(app, ctx, orchestrator)
    _register_git_routes(app, ctx, orchestrator)
    if component_installed("chat"):
        from ..chat import routes as chat_routes  # pyright: ignore[reportMissingImports]

        chat_routes.register_chat_routes(app, ctx, orchestrator)
    _register_preview_routes(app, ctx, orchestrator)
    if pm_installed():
        from ..pm import routes as pm_routes  # pyright: ignore[reportMissingImports]

        pm_routes.register_pm_routes(app, ctx, orchestrator)
        # PM browser assets ship with the optional PM distribution; register
        # them ahead of the core `/static/` route that has no `pm/` directory.
        pm_static = Path(pm_routes.__file__).parent / "static"
        if pm_static.is_dir():
            app.router.add_static("/static/pm/", pm_static, show_index=False)
    _register_project_routes(app, ctx)
    _register_meta_routes(app, ctx, orchestrator)
