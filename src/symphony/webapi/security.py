"""Request plumbing: loopback/origin/token gates, the `/api/` guard
middleware, JSON body parsing, and the handler error wrapper."""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web

from ..errors import SymphonyError
from ..logging import get_logger
from ..service_identity import (
    SERVICE_INSTANCE_HEADER,
    normalize_service_probe_credential,
)

log = get_logger()


STATIC_DIR = Path(__file__).parent.parent / "web" / "static"

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
# `ChatManager` mints these as <UTC date>-<UTC time>-<6 hex>.
_CHAT_SESSION_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_PROJECT_SETUP_ACTION_RE = re.compile(r"^project-[0-9a-f]{32}$")
_INTENT_ACTION_RE = re.compile(r"^intent-[0-9a-f]{32}$")
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
# PM Workspace routes refuse cross-origin mutations in `_api_guard`; the rest
# of the board keeps its narrower per-route origin checks (projects, chat ws).
_PM_API_PREFIX = "/api/v1/pm/"
_CI_EDITABLE_KEYS = {"enabled", "interval_ms", "max_turns", "agent_kind", "modes"}
BIND_HOST_KEY: web.AppKey[str] = web.AppKey("symphony.bind_host", str)
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


_FORWARDED_FOR_RE = re.compile(r"(?:^|[;,\s])for=([^;,\s]+)", re.IGNORECASE)


def _forwarded_client(request: web.Request) -> str | None:
    """Client address a reverse proxy reported, or None when neither
    `X-Forwarded-For` nor `Forwarded: for=` is present."""
    xff = request.headers.get("X-Forwarded-For")
    if xff is not None:
        return xff.split(",", 1)[0].strip()
    forwarded = request.headers.get("Forwarded")
    if forwarded is not None:
        match = _FORWARDED_FOR_RE.search(forwarded)
        return match.group(1).strip().strip('"') if match else ""
    return None


def _address_is_loopback(address: str) -> bool:
    """Accepts bare IPs plus the `[v6]:port` / `v4:port` forms proxies emit."""
    candidate = address.strip()
    if candidate.startswith("["):
        candidate = candidate.split("]", 1)[0].removeprefix("[")
    elif candidate.count(":") == 1:
        candidate = candidate.rsplit(":", 1)[0]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _request_is_loopback(request: web.Request) -> bool:
    """Keep sensitive run diagnostics on the operator's local machine.

    Behind a declared reverse proxy (`SYMPHONY_TRUSTED_ORIGINS` set) the TCP
    peer is the proxy itself, so the decision moves to the client address it
    forwarded; a missing, malformed, or non-loopback forwarded address is
    not loopback. Without trusted origins the headers are ignored — a
    direct loopback caller could forge them.
    """
    remote = request.remote
    if remote is None:
        bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
        return bind in _LOOPBACK_BINDS
    try:
        peer_is_loopback = ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False
    if not peer_is_loopback or not _trusted_origins():
        return peer_is_loopback
    forwarded = _forwarded_client(request)
    if forwarded is None:
        return True
    return _address_is_loopback(forwarded)


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
    for entry in trusted:
        try:
            candidate = urlsplit(entry).hostname or entry
        except ValueError:
            continue
        if bare == _bare_host(candidate):
            return True
    return False


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
    try:
        host = _bare_host(urlsplit(origin).hostname or "")
    except ValueError:
        return False
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


_BROWSER_PROVENANCE_HEADERS = ("Origin", "Referer", "Sec-Fetch-Site", "Sec-Fetch-Mode")


def _request_from_browser(request: web.Request) -> bool:
    """True when the request carries any header only a browser attaches.

    Browsers always send `Origin` on cross-origin POSTs and `Sec-Fetch-*`
    on every fetch; command-line clients send none of them.
    """
    return any(h in request.headers for h in _BROWSER_PROVENANCE_HEADERS)


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
        if request.method in {
            "POST", "PUT", "PATCH", "DELETE"
        } and request.path.startswith(_PM_API_PREFIX):
            origin = request.headers.get("Origin", "")
            referer = request.headers.get("Referer", "")
            malformed_referer = False
            if not origin and referer:
                try:
                    parsed_referer = urlsplit(referer)
                except ValueError:
                    malformed_referer = True
                else:
                    if parsed_referer.scheme and parsed_referer.netloc:
                        origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
            fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().lower()
            if (
                malformed_referer
                or (origin and not _origin_is_trusted(request, origin))
                or (fetch_site == "cross-site" and not origin)
            ):
                return _json_error(
                    403, "forbidden_origin", "cross-origin mutation is not allowed"
                )
        # A JSON content type is what forces the CORS preflight, so it is
        # required on every mutation a browser sends, including a body-less
        # POST from an HTML form. A request with no browser provenance
        # (plain `curl -X POST`, scripts) cannot be a CSRF vector and keeps
        # working without a body.
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.content_type != "application/json"
            and (request.body_exists or _request_from_browser(request))
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
