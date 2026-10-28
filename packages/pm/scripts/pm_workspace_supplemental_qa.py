#!/usr/bin/env python3
"""Supplemental installed-wheel PM browser/API evidence against an existing fixture."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
import os
import csv
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


class Client:
    def __init__(self, base_url: str, project_id: str, token: str = "") -> None:
        self.origin = base_url.rstrip("/")
        self.base = self.origin + "/api/v1/pm"
        self.project_id = project_id
        self.token = token

    def request(
        self, method: str, path: str, body: Any = None,
        *, headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, bytes]:
        payload = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if body is not None else None
        )
        request_headers = dict(headers or {})
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.origin + path, data=payload, method=method, headers=request_headers
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        try:
            parsed: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = raw.decode(errors="replace")
        return status, parsed, raw

    def pm(self, method: str, path: str, body: Any = None, **kwargs: Any) -> tuple[int, Any, bytes]:
        return self.request(method, "/api/v1/pm" + path, body, **kwargs)


def _record(receipts: dict[str, Any], objective: str, passed: bool, **details: Any) -> None:
    receipts[objective] = {
        "status": "executed_pass" if passed else "executed_fail",
        **details,
    }


def installed_binding() -> dict[str, Any]:
    try:
        core = importlib.metadata.distribution("oh-my-symphony")
        pm = importlib.metadata.distribution("oh-my-symphony-pm")
        owned: list[tuple[str, str]] = []
        for distribution in (core, pm):
            record = next(
                item for item in (distribution.files or ())
                if str(item).endswith(".dist-info/RECORD")
            )
            with Path(distribution.locate_file(record)).open(
                newline="", encoding="utf-8"
            ) as handle:
                for relative, _, _ in csv.reader(handle):
                    if not relative.startswith("symphony/"):
                        continue
                    candidate = Path(distribution.locate_file(relative))
                    if candidate.is_file():
                        owned.append((relative, hashlib.sha256(candidate.read_bytes()).hexdigest()))
        digest = hashlib.sha256(
            json.dumps(owned, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "version": core.version,
            "module_path": str(pm.locate_file("symphony/pm/__init__.py")),
            "package_digest": "sha256:" + digest,
            "verified_file_count": len(owned),
            "python": sys.executable,
        }
    except (importlib.metadata.PackageNotFoundError, StopIteration, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "python": sys.executable}


def _padded_workbook(data: bytes, padding_bytes: int, *, compressible: bool) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        padding = b"x" * padding_bytes if compressible else os.urandom(padding_bytes)
        target.writestr("qa/padding.bin", padding)
    return output.getvalue()


def api_boundaries(
    client: Client, change_id: str, receipts: dict[str, Any]
) -> None:
    validate_path = f"/changes/{change_id}/validate"
    envelope = {"project_id": client.project_id, "change_id": change_id}
    host_status, host_body, _ = client.pm(
        "GET", "/capabilities", headers={"Host": "evil.example"}
    )
    origin_status, origin_body, _ = client.pm(
        "POST", validate_path, envelope,
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    same_status, _, _ = client.pm(
        "POST", validate_path, envelope,
        headers={"Content-Type": "application/json", "Origin": client.origin},
    )
    _record(
        receipts, "O35", host_status == 403 and origin_status == 403 and same_status != 403,
        hostile_host={"status": host_status, "body": host_body},
        hostile_origin={"status": origin_status, "body": origin_body},
        same_origin_status=same_status,
    )

    import_path = f"/changes/{change_id}/imports/preview"
    multipart_status, multipart_body, _ = client.pm(
        "POST", import_path, b"--qa\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=qa"},
    )
    malformed_status, malformed_body, _ = client.pm(
        "POST", import_path,
        {**envelope, "payload": {"filename": "malformed.xlsx", "base64": "not valid base64!"}},
    )
    oversized_status, oversized_body, _ = client.pm(
        "POST", import_path,
        {**envelope, "payload": {"filename": "oversized.xlsx", "base64": "A" * (8 * 1024 * 1024 + 4)}},
    )
    unrelated_status, _, _ = client.request(
        "POST", "/api/v1/refresh", {"padding": "x" * 1_100_000}
    )

    change_status, change, _ = client.pm(
        "GET", f"/changes/{change_id}?project_id={urllib.parse.quote(client.project_id)}"
    )
    valid_large: dict[str, Any] = {"status": "not_run"}
    decoded_large: dict[str, Any] = {"status": "not_run"}
    if change_status == 200:
        export_status, exported, _ = client.pm(
            "POST", f"/changes/{change_id}/exports",
            {**envelope, "expected_revision": change["revision"], "format": "xlsx"},
        )
        if export_status == 201:
            file_status, _, workbook = client.pm(
                "GET", f"/exports/{exported['export_id']}/file"
            )
            if file_status == 200:
                over_global = _padded_workbook(workbook, 1_100_000, compressible=False)
                status, body, _ = client.pm(
                    "POST", import_path,
                    {**envelope, "payload": {"filename": "large.xlsx", "base64": base64.b64encode(over_global).decode()}},
                )
                valid_large = {"status": status, "bytes": len(over_global), "body": body}
                over_uncompressed = _padded_workbook(
                    workbook, 5 * 1024 * 1024 + 1, compressible=False
                )
                status, body, _ = client.pm(
                    "POST", import_path,
                    {**envelope, "payload": {"filename": "expanded.xlsx", "base64": base64.b64encode(over_uncompressed).decode()}},
                )
                decoded_large = {"status": status, "bytes": len(over_uncompressed), "body": body}
    passed = (
        multipart_status == 415
        and malformed_status == 400
        and oversized_status == 413
        and unrelated_status == 413
        and valid_large.get("status") == 200
        and decoded_large.get("status") == 413
        and decoded_large.get("body", {}).get("error", {}).get("code") == "payload_too_large"
    )
    _record(
        receipts, "O18", passed,
        multipart={"status": multipart_status, "body": multipart_body},
        malformed={"status": malformed_status, "body": malformed_body},
        encoded_over={"status": oversized_status, "body": oversized_body},
        valid_over_global=valid_large,
        decoded_over=decoded_large,
        unrelated_global_status=unrelated_status,
    )


def browser_scenarios(
    client: Client, change_id: str, evidence_root: Path,
    receipts: dict[str, Any], *, other_project: str = "",
) -> None:
    from playwright.sync_api import sync_playwright

    evidence_root.mkdir(parents=True, exist_ok=True)
    external_requests: list[str] = []
    with sync_playwright() as playwright:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(
            headless=True, executable_path=executable or None
        )
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        if client.token:
            page.add_init_script(
                "token => localStorage.setItem('symphony.apiToken', token)", client.token
            )
        page.add_init_script("localStorage.setItem('symphony.lang', 'ko')")
        page.on(
            "request",
            lambda request: external_requests.append(request.url)
            if "example.invalid" in request.url else None,
        )
        url = f"{client.origin}/?pmTab=plan&pmChange={change_id}#/artifacts"
        page.goto(url, wait_until="networkidle")
        page.get_by_role("heading", name="기획 산출물", exact=True).wait_for()
        wbs_status, wbs, _ = client.pm(
            "GET", f"/changes/{change_id}/wbs?project_id={urllib.parse.quote(client.project_id)}"
        )
        assert wbs_status == 200 and wbs["tasks"]
        root_task = next(item for item in wbs["tasks"] if item.get("executable", True))

        row = page.locator(f'[data-task-id="{root_task["task_id"]}"]').first
        if row.count() == 0:
            row = page.locator(".tabulator-row").filter(
                has_text=root_task["task_id"]
            ).first
        row.focus()
        row.press("Enter")
        drawer = page.get_by_role("dialog", name=f'Task {root_task["task_id"]}')
        drawer.wait_for()
        drawer.press("Escape")
        assert page.evaluate("id => document.activeElement?.dataset?.taskId === id || document.activeElement?.textContent?.includes(id)", root_task["task_id"])

        hierarchy_parent = next(
            (item for item in wbs["tasks"] if not item.get("executable", True)),
            root_task,
        )
        parent_row = page.locator(".tabulator-row").filter(
            has_text=hierarchy_parent["task_id"]
        ).first
        parent_row.focus()
        parent_row.press("Enter")
        parent_drawer = page.get_by_role(
            "dialog", name=f'Task {hierarchy_parent["task_id"]}'
        )
        parent_drawer.wait_for()
        parent_drawer.press("Escape")
        page.get_by_role("button", name="하위 추가", exact=True).focus()
        with page.expect_response(
            lambda response: response.request.method == "PATCH" and response.url.endswith("/wbs")
        ) as created_info:
            page.keyboard.press("Enter")
        if created_info.value.status != 200:
            raise AssertionError(
                f"keyboard child creation failed: HTTP {created_info.value.status}: {created_info.value.text()}"
            )
        created_payload = created_info.value.json()
        child_id = created_payload["impacted_ids"][0]
        page.wait_for_function(
            "id => document.body.innerText.includes(id)", arg=child_id
        )
        child = page.locator(f'[data-task-id="{child_id}"]').first
        if child.count() == 0:
            child = page.locator(".tabulator-row").filter(has_text=child_id).first
        child.focus()
        child.press("Enter")
        child_drawer = page.get_by_role("dialog", name=f"Task {child_id}")
        child_drawer.wait_for()
        child_drawer.press("Escape")
        page.get_by_role("button", name="실행 취소", exact=True).focus()
        with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/wbs")):
            page.keyboard.press("Enter")
        page.wait_for_function("id => !document.body.innerText.includes(id)", arg=child_id)
        page.get_by_role("button", name="다시 실행", exact=True).focus()
        with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/wbs")):
            page.keyboard.press("Enter")
        page.wait_for_function("id => document.body.innerText.includes(id)", arg=child_id)
        _record(receipts, "O7", True, root_task=hierarchy_parent["task_id"], child_task=child_id, focus_restored=True, undo_redo_same_id=True)

        tsv = f'"{root_task["task_id"]}"\t"notes"\t"첫 줄\n둘째 줄"\n'
        page.locator(".pm-tabulator").evaluate(
            """(target, text) => { const data = new DataTransfer(); data.setData('text/plain', text); target.dispatchEvent(new ClipboardEvent('paste', {clipboardData:data,bubbles:true,cancelable:true})); }""",
            tsv,
        )
        preview = page.get_by_role("dialog", name="Paste preview")
        preview.wait_for()
        assert "첫 줄" in preview.inner_text() and "둘째 줄" in preview.inner_text()
        with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/wbs")) as paste_info:
            page.get_by_role("button", name="Apply 1 rows", exact=True).click()
        assert paste_info.value.status == 200
        _, updated_wbs, _ = client.pm(
            "GET", f"/changes/{change_id}/wbs?project_id={urllib.parse.quote(client.project_id)}"
        )
        updated_task = next(item for item in updated_wbs["tasks"] if item["task_id"] == root_task["task_id"])
        assert updated_task["notes"] == "첫 줄\n둘째 줄"
        _record(receipts, "O8", True, text=updated_task["notes"], transactional_preview=True)
        _record(receipts, "O44", True, language=page.locator("html").get_attribute("lang"), keyboard_actions=["open_drawer", "escape", "add_child", "undo", "redo"])

        change_status, change, _ = client.pm(
            "GET", f"/changes/{change_id}?project_id={urllib.parse.quote(client.project_id)}"
        )
        assert change_status == 200
        design_status, design, _ = client.pm(
            "GET", f"/changes/{change_id}/documents/design?project_id={urllib.parse.quote(client.project_id)}"
        )
        assert design_status == 200
        hostile = design["source"].rstrip() + """

## Architecture
<script>window.__pmQaExecuted = true</script>
```mermaid
flowchart LR
A --> B
click A "https://example.invalid/leak"
```
"""
        status, patched, _ = client.pm(
            "PATCH", f"/changes/{change_id}/documents/design",
            {"project_id": client.project_id, "change_id": change_id, "expected_revision": change["revision"], "client_operation_id": "qa-hostile-diagram", "payload": {"operations": [{"op": "replace_source", "source": hostile}]}},
        )
        assert status == 200
        page.goto(f"{client.origin}/?pmTab=design&pmView=architecture&pmChange={change_id}#/artifacts", wait_until="networkidle")
        page.get_by_role("heading", name="기획 산출물", exact=True).wait_for()
        body_text = page.locator(".pm-body").inner_text()
        executed = page.evaluate("Boolean(window.__pmQaExecuted)")
        _record(
            receipts, "O43",
            not executed and not external_requests,
            script_executed=executed, external_requests=external_requests,
            text_alternative_visible="Diagram" in body_text or "다이어그램" in body_text,
        )
        latest_status, latest, _ = client.pm(
            "GET", f"/changes/{change_id}?project_id={urllib.parse.quote(client.project_id)}"
        )
        assert latest_status == 200
        restore_status, _, _ = client.pm(
            "PATCH", f"/changes/{change_id}/documents/design",
            {"project_id": client.project_id, "change_id": change_id, "expected_revision": latest["revision"], "client_operation_id": "qa-restore-design", "payload": {"operations": [{"op": "replace_source", "source": design["source"]}]}},
        )
        assert restore_status == 200

        page.goto(f"{client.origin}/?pmTab=requirements&pmChange={change_id}#/artifacts", wait_until="networkidle")
        delta = page.locator(".pm-requirement-delta")
        delta.wait_for()
        baseline_text = delta.locator(".pm-baseline").inner_text()
        proposed_text = delta.locator(".pm-document-preview").first.inner_text()
        has_existing_baseline = "new capability" not in baseline_text.lower()
        _record(
            receipts, "O3", has_existing_baseline,
            baseline_text=baseline_text[:500], proposed_text=proposed_text[:500],
        )

        page.get_by_role("button", name="코드 파일", exact=True).click()
        artifact = page.locator(".pm-evidence-link").first
        if artifact.count():
            with page.expect_download() as download_info:
                artifact.click()
            target = evidence_root / download_info.value.suggested_filename
            download_info.value.save_as(target)
            _record(receipts, "O30", True, filename=target.name, sha256="sha256:" + hashlib.sha256(target.read_bytes()).hexdigest())
        else:
            receipts["O30"] = {"status": "pending", "reason": "fixture has no ArtifactStore-backed receipt link"}

        page.screenshot(path=str(evidence_root / "supplemental-ko.png"), full_page=True)
        if other_project:
            page.goto(f"{client.origin}/?pmTab=idea&pmChange={change_id}#/artifacts", wait_until="networkidle")
            page.get_by_role("button", name="아이디어", exact=True).click()
            page.get_by_role("button", name="소스 편집", exact=True).click()
            editor = page.get_by_label("proposal source")
            editor.fill(editor.input_value() + "\nunsaved")
            answers = iter((True, False))
            def cancel_second_prompt(dialog: Any) -> None:
                if next(answers):
                    dialog.accept()
                else:
                    dialog.dismiss()
            page.on("dialog", cancel_second_prompt)
            old_url = page.url
            page.locator("#project-selector").select_option(other_project)
            page.wait_for_timeout(500)
            cancelled = page.url == old_url
            page.remove_listener("dialog", cancel_second_prompt)

            race = browser.new_page(viewport={"width": 1366, "height": 768})
            race.add_init_script(
                """const original = window.fetch.bind(window); window.fetch = (...args) => { const result = original(...args); const url = String(args[0]); return url.includes('/api/v1/pm/changes?') ? result.then(async response => { await new Promise(resolve => setTimeout(resolve, 1500)); return response; }) : result; };"""
            )
            race.goto(url, wait_until="domcontentloaded")
            race.locator(f'#project-selector option[value="{other_project}"]').wait_for(
                state="attached"
            )
            race_old_url = race.url
            race.locator("#project-selector").select_option(other_project)
            race.wait_for_url(
                lambda current: str(current) != race_old_url, timeout=10_000
            )
            race.locator(f'#project-selector option[value="{other_project}"]').wait_for(
                state="attached"
            )
            race.wait_for_timeout(2_000)
            switched = race.locator("#project-selector").input_value() == other_project
            _record(
                receipts, "O1", cancelled and switched,
                cancelled_url=page.url, original_url=old_url,
                switched_url=race.url, stale_response_discarded=switched,
            )
            race.close()
        else:
            receipts["O1"] = {"status": "pending", "reason": "second registered project URL not supplied"}
        browser.close()


def disabled_recovery(
    client: Client, change_id: str, receipts: dict[str, Any]
) -> None:
    status, capabilities, _ = client.pm("GET", "/capabilities")
    change_status, change, _ = client.pm(
        "GET", f"/changes/{change_id}?project_id={urllib.parse.quote(client.project_id)}"
    )
    document_status = 0
    mutation_status = 0
    mutation_body: Any = None
    if change_status == 200:
        document_status, _, _ = client.pm(
            "GET", f"/changes/{change_id}/documents/proposal?project_id={urllib.parse.quote(client.project_id)}"
        )
        mutation_status, mutation_body, _ = client.pm(
            "PATCH", f"/changes/{change_id}/documents/proposal",
            {"project_id": client.project_id, "change_id": change_id, "expected_revision": change["revision"], "payload": {"operations": []}},
        )
    passed = (
        status == 200
        and not any(capabilities.get("flags", {}).values())
        and change_status == 200
        and document_status == 200
        and mutation_status == 503
        and isinstance(mutation_body, dict)
        and mutation_body.get("error", {}).get("code") == "capability_disabled"
    )
    _record(
        receipts, "O40", passed,
        flags=capabilities.get("flags") if status == 200 else None,
        change_status=change_status, document_status=document_status,
        mutation={"status": mutation_status, "body": mutation_body},
        guidance=capabilities.get("health") if status == 200 else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--other-project-id", default="")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--disabled-url", default="")
    parser.add_argument("--disabled-project-id", default="")
    parser.add_argument("--disabled-change-id", default="")
    args = parser.parse_args()
    receipts: dict[str, Any] = {}
    client = Client(args.url, args.project_id, args.token)
    api_boundaries(client, args.change_id, receipts)
    if not args.api_only:
        browser_scenarios(
            client, args.change_id, args.evidence_root, receipts,
            other_project=args.other_project_id,
        )
    if args.disabled_url and args.disabled_project_id and args.disabled_change_id:
        disabled_recovery(
            Client(args.disabled_url, args.disabled_project_id, args.token),
            args.disabled_change_id, receipts,
        )
    else:
        receipts["O40"] = {
            "status": "pending",
            "reason": "disabled-mode server over the preserved planning workspace not supplied",
        }
    payload = {
        "installation": installed_binding(),
        "base_url": args.url,
        "project_id": args.project_id,
        "change_id": args.change_id,
        "objectives": receipts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value["status"] for key, value in receipts.items()}, sort_keys=True))
    return 0 if all(value["status"] != "executed_fail" for value in receipts.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
