#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


class SmokeFailure(RuntimeError):
    pass


def request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, _parse_response(text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        return exc.code, _parse_response(text)


def _parse_response(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def expect(
    base_url: str,
    method: str,
    path: str,
    status: int,
    body: dict[str, Any] | None = None,
) -> Any:
    actual, payload = request(base_url, method, path, body)
    if actual != status:
        raise SmokeFailure(
            f"{method} {path}: expected {status}, got {actual}, body={payload!r}. "
            f"Next step: {_diagnostic_hint(path)}"
        )
    return payload


def _diagnostic_hint(path: str) -> str:
    if path == "/api/v1/health":
        return "check `symphony service status <workflow>` and server logs"
    return "check `symphony service status <workflow>` and rerun with server logs open"


def _check_health(base_url: str) -> None:
    health = expect(base_url, "GET", "/api/v1/health", 200)
    status = health.get("status") if isinstance(health, dict) else None
    if status in {"ok", "starting"}:
        return
    reasons = (
        health.get("degraded_reasons", [])
        if isinstance(health, dict)
        else ["invalid health payload"]
    )
    raise SmokeFailure(
        "GET /api/v1/health: service is degraded "
        f"({', '.join(str(r) for r in reasons) or 'unknown reason'}). "
        f"Next step: {_diagnostic_hint('/api/v1/health')}"
    )


def run_smoke(
    base_url: str,
    *,
    prefix: str = "SMOKE",
    learn_id: str = "",
) -> list[Check]:
    checks: list[Check] = []
    created: list[str] = []

    def ok(name: str) -> None:
        checks.append(Check(name, True))

    try:
        _check_health(base_url)
        ok("health")

        state = expect(base_url, "GET", "/api/v1/state", 200)
        if "counts" not in state:
            raise SmokeFailure("state payload missing counts")
        ok("state")

        board = expect(base_url, "GET", "/api/v1/board", 200)
        if not board["columns"]:
            raise SmokeFailure("board has no columns")
        ok("board")

        app_js_status, app_js = request(base_url, "GET", "/static/app.js")
        if app_js_status != 200 or "boardScope" not in str(app_js):
            raise SmokeFailure("static app.js missing boardScope")
        ok("static assets")

        stamp = str(int(time.time() * 1000))[-8:]
        issue = f"{prefix}{stamp}"
        created.append(issue)
        expect(
            base_url,
            "POST",
            "/api/v1/issues",
            201,
            {
                "identifier": issue,
                "title": "API smoke card",
                "state": "Human Review",
                "labels": ["smoke"],
                "description": "Created by smoke_web_api.py.",
            },
        )
        ok("issue create")

        detail = expect(base_url, "GET", f"/api/v1/issues/{issue}", 200)
        if detail["description"] != "Created by smoke_web_api.py.":
            raise SmokeFailure("detail description mismatch")
        ok("issue detail")

        expect(
            base_url,
            "PATCH",
            f"/api/v1/issues/{issue}",
            200,
            {"state": "Done", "title": "API smoke done"},
        )
        detail = expect(base_url, "GET", f"/api/v1/issues/{issue}", 200)
        if detail["state"] != "Done" or detail["title"] != "API smoke done":
            raise SmokeFailure("patch did not persist")
        ok("issue patch")

        if learn_id:
            expect(base_url, "POST", f"/api/v1/{learn_id}/skip-learn", 200)
            ok("skip learn")

        expect(base_url, "GET", "/api/v1/refresh", 405)
        expect(base_url, "POST", "/api/v1/refresh", 202)
        ok("refresh")

        expect(base_url, "GET", "/api/v1/workflow", 200)
        expect(base_url, "GET", "/api/v1/stats?days=7", 200)
        expect(base_url, "GET", "/api/v1/skills", 404)
        ok("workflow stats skills")
    finally:
        for identifier in created:
            request(base_url, "DELETE", f"/api/v1/issues/{identifier}")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test a running Symphony web/API server."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:9999")
    parser.add_argument("--prefix", default="SMOKE")
    parser.add_argument(
        "--learn-id",
        default="",
        help="Optional idle Learn issue to exercise skip-learn",
    )
    args = parser.parse_args()

    checks = run_smoke(args.base_url, prefix=args.prefix, learn_id=args.learn_id)
    for check in checks:
        print(f"ok {check.name}")
    print(json.dumps({"count": len(checks), "checks": [c.name for c in checks]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
