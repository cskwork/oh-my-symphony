"""Installed-wheel PM journeys. Required Node proof enables the browser run too.

By default this remains opt-in via SYMPHONY_BROWSER_E2E=1. A clean wheel venv
is built once per session from the core and PM distributions;
SYMPHONY_PM_DEMO_WHEEL can reuse wheel files (os.pathsep-separated), including
a separately built base-code wheel for the red proof. Both paths create a clean
venv and install only the wheels and their dependencies; neither
installs pm-exchange. All generated projects and hostile config live in pytest
temporary folders, never in an SDLC scratch fixture.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest
import yaml

from tests._pm_support import skip_or_fail

pytestmark = pytest.mark.browser_e2e
ROOT = Path(__file__).resolve().parents[1]
REQUIRED = "SYMPHONY_PM_NODE_REQUIRED"
CLEAN_KEYS = {
    "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
    "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT", "PYTHONPATH", "VIRTUAL_ENV",
}
# D7 labels, independently pinned in both user-facing languages.
LABELS = {
    "workspace": [("New change", "새 변경"), ("Repair workspace", "작업공간 복구"),
                  ("Convert legacy plan", "기존 계획 변환"),
                  ("Edit in external editor", "외부 편집기로 열기"),
                  ("Validate", "검증"), ("Approve revision", "리비전 승인")],
    "idea": [("Edit source", "소스 편집"), ("Save draft", "초안 저장"),
             ("Planning assistant", "기획 도우미"), ("Add decision", "결정 추가")],
    "requirements": [("Add capability", "기능 요구사항 추가"),
                     ("Edit requirements", "요구사항 편집"),
                     ("Add requirement", "요구사항 추가"), ("Add scenario", "시나리오 추가"),
                     ("Waive coverage", "커버리지 면제"), ("Revoke waiver", "면제 취소")],
    "design": [("Storyboard", "스토리보드"), ("Save storyboard", "스토리보드 저장"),
               ("Add storyboard", "스토리보드 추가"), ("Add contract", "계약 추가")],
    "plan": [("Work breakdown", "작업 분해"), ("Dependencies", "의존성"),
             ("Add group", "그룹 추가"), ("Add child", "하위 추가"),
             ("Delete", "삭제"), ("Cancel task", "작업 취소"), ("Undo", "실행 취소"),
             ("Download export", "내보내기 다운로드"), ("Preview import", "가져오기 미리보기"),
             ("Preview approved handoff", "승인된 전달 미리보기"),
             ("Preview archive", "아카이브 미리보기")],
    "code": [("Run verification", "검증 실행"), ("Submit completion evidence", "완료 증거 제출"),
             ("Review Git commit", "Git 커밋 검토"), ("Open run evidence", "실행 증거 열기"),
             ("Accept verified revision", "검증된 리비전 인수")],
}
TABS = {"idea": ("Idea", "아이디어"), "requirements": ("Requirements", "요구사항"),
        "design": ("Design", "설계"), "plan": ("Plan", "계획"), "code": ("Code Files", "코드 파일")}


def _label(text, lang):
    return next((ko for entries in LABELS.values() for en, ko in entries if en == text), text) if lang == "ko" else text


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in CLEAN_KEYS or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))


def _unavailable(reason) -> NoReturn:
    # An explicit browser opt-in or required Node run cannot succeed by skipping.
    flag = REQUIRED if os.environ.get(REQUIRED) == "1" else "SYMPHONY_BROWSER_E2E"
    skip_or_fail(reason, flag=flag)


@pytest.fixture(scope="session")
def installed_python(tmp_path_factory):
    if os.environ.get(REQUIRED) != "1" and os.environ.get("SYMPHONY_BROWSER_E2E") != "1":
        _unavailable("Set SYMPHONY_BROWSER_E2E=1 or SYMPHONY_PM_NODE_REQUIRED=1 for installed Chrome proof")
    node = shutil.which("node")
    if not node:
        _unavailable("PM browser demo needs Node.js 20.19.0 or newer on PATH (found: none)")
    result = subprocess.run([node, "-p", "process.version"], capture_output=True, text=True, timeout=10)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    if result.returncode or not match or tuple(map(int, match.groups())) < (20, 19, 0):
        _unavailable(f"PM browser demo needs Node.js 20.19.0 or newer (found: {result.stdout.strip()})")
    builder = shutil.which("python3.12")
    if not builder:
        _unavailable("Installed PM browser proof needs python3.12")
    folder = tmp_path_factory.mktemp("pm-installed-wheel")
    reused = os.environ.get("SYMPHONY_PM_DEMO_WHEEL")
    if reused:
        # Core and PM wheels, separated by os.pathsep (a base-code wheel alone
        # predates the PM split and carries both).
        wheel_files = [Path(item).absolute() for item in reused.split(os.pathsep) if item]
        assert all(item.is_file() and item.suffix == ".whl" for item in wheel_files), wheel_files
    else:
        wheels = folder / "wheel"
        wheels.mkdir()
        # setuptools builds in place; parallel workers need separate source trees.
        source = folder / "source"
        source.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(
            ROOT / "src", source / "src",
            ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
        )
        pm_source = folder / "pm-source"
        pm_source.mkdir()
        for name in ("pyproject.toml", "README.md"):
            shutil.copy2(ROOT / "packages/pm" / name, pm_source / name)
        shutil.copytree(
            ROOT / "packages/pm/src", pm_source / "src",
            ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
        )
        _command([builder, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheels)], source, folder / "wheel.log")
        _command([builder, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheels)], pm_source, folder / "pm-wheel.log")
        core_wheel, = wheels.glob("oh_my_symphony-*.whl")
        pm_wheel, = wheels.glob("oh_my_symphony_pm-*.whl")
        wheel_files = [core_wheel, pm_wheel]
    _command([builder, "-m", "venv", str(folder / "venv")], folder, folder / "venv.log")
    executable = folder / "venv/bin/python"
    _command([str(executable), "-m", "pip", "install", "--force-reinstall", *map(str, wheel_files)], folder, folder / "install.log")
    assert executable.is_file(), executable
    check = subprocess.run([str(executable), "-c", "import importlib.util, symphony; print(symphony.__file__); assert importlib.util.find_spec('openpyxl') is None"],
                           capture_output=True, text=True, timeout=15, cwd=executable.parent,
                           env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
    assert check.returncode == 0, check.stdout + check.stderr
    assert "site-packages" in check.stdout, check.stdout
    print(f"Installed PM wheel Python: {executable}; module: {check.stdout.strip()}; pm-exchange absent")
    help_result = subprocess.run([str(executable.parent / "symphony"), "pm", "demo", "--help"],
                                 cwd=folder, capture_output=True, text=True, timeout=15)
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    return executable


def _command(argv, cwd, log):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=180,
                            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, f"{argv!r}: {log.read_text()}"


@pytest.fixture
def chrome(installed_python):
    try:
        api = importlib.import_module("playwright.sync_api")
    except ImportError:
        _unavailable("Installed PM browser proof needs Playwright in the test runner")
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except api.Error as exc:
            message = str(exc)
            if any(text in message for text in ["Operation not permitted", "Permission denied", "EPERM"]):
                pytest.fail(f"SANDBOX-RERUN: Chrome launch denied: {message}")
            if "executable doesn't exist" in message.lower() or "not found" in message.lower():
                _unavailable(f"Installed PM browser proof needs installed Google Chrome: {message}")
            raise
        try:
            yield browser
        finally:
            browser.close()


def _environment(tmp_path, **overrides):
    env = {key: value for key, value in os.environ.items()
           if key not in CLEAN_KEYS and not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), PYTHONUNBUFFERED="1", OPENSPEC_TELEMETRY="0")
    env.update(overrides)
    return env


@contextmanager
def _server(argv, tmp_path, env, *, url=None):
    log = tmp_path / ("ordinary.log" if url else "demo.log")
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(argv, cwd=tmp_path, env=env, stdout=output, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                assert process.poll() is None, f"Server exited {process.returncode}: {log.read_text()}"
                if url is None:
                    match = re.search(r"^Open: (\S+)$", log.read_text(), re.MULTILINE)
                    if match:
                        url = match.group(1)
                        parsed = urlparse(url)
                        assert parsed.hostname == "127.0.0.1" and parsed.port, url
                if url:
                    try:
                        with urlopen(url.split("/?")[0] + "/", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                time.sleep(0.05)
            else:
                pytest.fail(f"Server did not become ready: {log.read_text()}")
            yield process, url
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                returncode = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                pytest.fail(f"SIGINT did not stop server: {log.read_text()}")
            assert returncode == 0, f"SIGINT exit {returncode}: {log.read_text()}"


def _hostile_git(tmp_path):
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    for name in ["pre-commit", "post-checkout"]:
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n", encoding="utf-8")
        hook.chmod(0o755)
    template = tmp_path / "hostile-template"
    template.mkdir()
    shutil.copytree(hooks, template / "hooks")
    config = tmp_path / "hostile.gitconfig"
    config.write_text(f'[init]\n templateDir = "{template}"\n[core]\n hooksPath = "{hooks}"\n[commit]\n gpgsign = true\n[tag]\n gpgsign = true\n', encoding="utf-8")
    return config, marker


@contextmanager
def _page(chrome, token="", *, preset_english=False, confirm_response=None):
    context = chrome.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    denied = []
    dialogs = []

    def route(request_route):
        if urlparse(request_route.request.url).hostname != "127.0.0.1":
            denied.append(request_route.request.url)
            request_route.abort()
        else:
            request_route.continue_()

    context.route("**/*", route)
    page = context.new_page()
    if preset_english:
        # One-shot setup: reloading after a user's choice must not reset storage.
        page.add_init_script("if (!sessionStorage.getItem('pm-test-preset')) { localStorage.setItem('symphony.lang', 'en'); sessionStorage.setItem('pm-test-preset', '1'); }")

    def dialog(item):
        dialogs.append((item.type, item.message))
        if item.type == "confirm" and confirm_response and not confirm_response():
            item.dismiss()
        else:
            item.accept(token if item.type == "prompt" else None)

    page.on("dialog", dialog)
    try:
        yield page, dialogs
    finally:
        context.close()
        assert not denied, f"Non-local browser requests: {denied}"


def _button(page, name, lang):
    # PM's segmented HTML buttons expose the accessible tab role.
    role = "tab" if name in {"Storyboard", "Work breakdown", "Dependencies"} else "button"
    return page.get_by_role(role, name=_label(name, lang), exact=True)


def _tab(page, area, lang):
    from playwright.sync_api import expect
    name = TABS[area][lang == "ko"]
    tab = page.locator(".pm-tabs").get_by_role("button", name=name, exact=True)
    tab.click()
    # selectTab renders this marker only after its panel hydration completes.
    expect(tab).to_have_attribute("aria-current", "page")


def _planning_state_label(value, lang):
    labels = {
        "Draft": "초안",
        "Validated": "검증됨",
        "NeedsReview": "검토 필요",
        "Approved": "승인됨",
        "Stale": "오래됨",
        "Archived": "보관됨",
    }
    return labels.get(value, value) if lang == "ko" else value


def _open_pm_tools(page, key):
    from playwright.sync_api import expect
    assert key in {"change", "revision", "wbs", "design"}
    owner = page.locator(f"details.pm-more-{key}")
    expect(owner).to_have_count(1)
    summary = owner.locator("summary").first
    expect(summary).to_be_visible()
    if owner.get_attribute("open") is None:
        summary.click()
    expect(owner).to_have_attribute("open", "")


def _guides(page, area, lang, matched):
    from playwright.sync_api import expect
    guides = page.locator("details.pm-guide")
    expect(guides).to_have_count(2)
    for index, key in enumerate(["workspace", area]):
        guide = guides.nth(index)
        if not guide.evaluate("node => node.open"):
            expect(guide).to_have_js_property("open", False)
            guide.locator("summary").focus()
            page.keyboard.press("Enter")
        expect(guide).to_have_attribute("open", "")
        expect(guide.locator("summary")).to_have_text("이 버튼들은 무엇을 하나요?" if lang == "ko" else "What do these buttons do?")
        expected = [entry[lang == "ko"] for entry in LABELS[key]]
        expect(guide.locator("dt")).to_have_text(expected)
        sentences = guide.locator("dd").all_text_contents()
        assert len(sentences) == len(expected) and all(sentences)
        if lang == "ko":
            assert all(re.search(r"[가-힣]", sentence) for sentence in sentences)
        for en, _ in LABELS[key]:
            buttons = _button(page, en, lang)
            if buttons.count():
                expect(buttons.first).to_have_js_property("tagName", "BUTTON")
                expect(buttons.first).to_have_text(_label(en, lang))
                matched.add(en)


def _runs(directory):
    with sqlite3.connect(directory / "project/.symphony/state.db") as database:
        return database.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_installed_demo_r7_journey(tmp_path, installed_python, chrome, lang):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    token = "pm-demo-browser-token" if lang == "ko" else ""
    overrides = {}
    marker = None
    if lang == "ko":
        config, marker = _hostile_git(tmp_path)
        overrides.update(SYMPHONY_API_TOKEN=token, SYMPHONY_TRUSTED_ORIGINS="https://evil.example", GIT_CONFIG_GLOBAL=str(config))
    env = _environment(tmp_path, **overrides)
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    matched = set()
    with _server(argv, tmp_path, env) as (_, url), _page(chrome, token, preset_english=lang == "ko") as (page, dialogs):
        page.goto(url)
        if token:
            # app.js authenticates through its tab-scoped HTML banner.
            banner = page.locator(".api-token-banner")
            expect(banner).to_be_visible()
            banner.get_by_label("API 토큰" if lang == "ko" else "API token", exact=True).fill(token)
            with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as connected:
                banner.get_by_role("button", name="연결" if lang == "ko" else "Connect", exact=True).click()
            assert connected.value.request.headers["authorization"] == f"Bearer {token}"
            expect(banner).to_have_count(0)
            # Connect refreshes the board; revisit PM with the saved tab token.
            page.reload()
        _open_pm_tools(page, "revision")
        expect(_button(page, "Validate", lang)).to_be_visible()
        assert page.evaluate("window.i18n.lang") == lang
        assert page.evaluate("localStorage.getItem('symphony.lang')") == lang
        assert "lang=" not in page.url
        if token:
            assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == token
            expect(page.locator(".api-token-banner")).to_have_count(0)
        _open_pm_tools(page, "change")
        _guides(page, "idea", lang, matched)
        _button(page, "Edit source", lang).click()
        expect(page.locator(".pm-view-switcher button")).to_have_text("문서 검토" if lang == "ko" else "Review document")
        expect(page.locator(".pm-source-editor")).to_be_focused()
        _guides(page, "idea", lang, matched)
        expect(_button(page, "Planning assistant", lang)).to_be_disabled()
        expect(_button(page, "Planning assistant", lang)).to_have_attribute("title", "Off in the demo: it would contact an AI model.")
        page.locator('.pm-view-switcher button[aria-pressed="true"]').click()
        expect(page.locator('.pm-view-switcher button[aria-pressed="false"]')).to_be_visible()
        expect(page.locator(".pm-view-switcher button")).to_have_text("소스 편집" if lang == "ko" else "Edit source")
        expect(page.locator('.pm-editor-pane')).to_be_hidden()
        # R7.1: the real combined validation, including OpenSpec, is ready.
        with page.expect_response(lambda response: response.url.endswith("/validate") and response.request.method == "POST") as validation:
            _button(page, "Validate", lang).click()
        report = validation.value.json()
        assert report["valid"] and report["source"] == "combined"
        expect(page.get_by_role("heading", name="검토 준비 완료" if lang == "ko" else "Ready for review", exact=True)).to_be_visible()
        page.get_by_role("button", name="닫기" if lang == "ko" else "Close", exact=True).click()
        _tab(page, "plan", lang)
        _open_pm_tools(page, "wbs")
        _guides(page, "plan", lang, matched)
        rows = page.locator(".pm-tabulator .tabulator-row")
        expect(rows).to_have_count(7)
        before = rows.all_text_contents()
        first_id = rows.first.locator('[tabulator-field="task_id"]').inner_text()
        assert first_id.startswith("G-")
        rows.first.locator('[tabulator-field="task_id"]').click()
        page.get_by_role("dialog", name=f"Task {first_id}", exact=True).get_by_role("button", name="닫기" if lang == "ko" else "Close", exact=True).click()
        # R7.2–3: add a child under a real group, undo, preserve every row/id.
        with page.expect_response(lambda response: response.url.endswith("/wbs") and response.request.method == "PATCH") as added:
            _button(page, "Add child", lang).click()
        child_id = added.value.json()["impacted_ids"][0]
        assert added.value.request.post_data_json["payload"]["operations"][0]["task"]["parent_id"] == first_id
        expect(rows).to_have_count(len(before) + 1)
        assert first_id and child_id
        with page.expect_response(lambda response: response.url.endswith("/wbs") and response.request.method == "PATCH"):
            _button(page, "Undo", lang).click()
        expect(rows).to_have_count(len(before))
        assert rows.all_text_contents() == before
        # R7.4: approval through the UI creates the hardened local Git commit.
        with page.expect_response(lambda response: response.url.endswith("/approvals") and response.request.method == "POST") as approval:
            _button(page, "Approve revision", lang).click()
        assert approval.value.status == 201
        expect(page.locator(".pm-revision-bar .pm-state")).to_have_text(_planning_state_label("Approved", lang))
        expect(_button(page, "Cancel task", lang)).to_be_visible()
        expect(_button(page, "Delete", lang)).to_have_count(0)
        git = subprocess.run(["git", "-C", str(directory / "planning"), "show", "-s", "--format=%an <%ae>%n%s", "HEAD"], env=env, capture_output=True, text=True, check=True)
        author, subject = git.stdout.strip().split("\n")
        assert author == "Symphony PM Demo <pm-demo@example.invalid>"
        assert subject.startswith("Approve PM plan team-leave-request (")
        _guides(page, "plan", lang, matched)
        # R7.5–6: preview alone does not publish, confirmation writes five Todo.
        _button(page, "Preview approved handoff", lang).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        assert not list((directory / "project/kanban").glob("*.md"))
        with page.expect_response(lambda response: response.url.endswith("/handoffs") and response.request.method == "POST") as published:
            page.get_by_role("button", name="네이티브 티켓 생성" if lang == "ko" else "Create native tickets", exact=True).click()
        assert published.value.status == 201
        tickets = list((directory / "project/kanban").glob("*.md"))
        assert len(tickets) == 5
        assert all(yaml.safe_load(ticket.read_text().split("---", 2)[1])["state"] == "Todo" for ticket in tickets)
        # R7.7: two real downloaded files; XLSX remains unavailable in this venv.
        export_format = page.get_by_label("내보내기 형식" if lang == "ko" else "Export format", exact=True)
        expect(export_format.locator("option[value=xlsx]")).to_be_disabled()
        expect(_button(page, "Preview import", lang)).to_be_disabled()
        for format_name in ["csv", "markdown"]:
            export_format.select_option(format_name)
            with page.expect_download() as download:
                _button(page, "Download export", lang).click()
            target = tmp_path / download.value.suggested_filename
            download.value.save_as(target)
            assert target.stat().st_size > 0
        # R7.8: no completed work means a real, rendered archive refusal.
        with page.expect_response(lambda response: response.url.endswith("/archive/preview")) as archive:
            _button(page, "Preview archive", lang).click()
        assert archive.value.status == 409
        assert archive.value.json()["error"]["code"] == "acceptance_incomplete"
        expect(page.locator(".pm-action-error")).to_contain_text("acceptance_incomplete")
        _inspect_other_guides(page, lang, matched)
        assert _runs(directory) == 0
        page.screenshot(path=str(tmp_path / f"demo-{lang}.png"), full_page=True)
        _inspect_fixture_guides(page, lang, matched, tmp_path)
        assert _runs(directory) == 0
        if lang == "ko":
            # R10.context: use the user's settings language picker, then reload.
            page.goto(url.split("/?")[0] + "/#/settings")
            language = page.locator("select").filter(has=page.locator("option[value=ko]"))
            expect(language).to_have_count(1)
            language.select_option("en")
            page.reload()
            assert page.evaluate("window.i18n.lang") == "en"
            page.goto(url.split("/?")[0] + "/#/artifacts")
            _open_pm_tools(page, "revision")
            expect(_button(page, "Validate", "en")).to_be_visible()
        assert len([kind for kind, _ in dialogs if kind == "confirm"]) >= 1
    assert list((tmp_path / "home").iterdir()) == []
    if marker:
        assert not marker.exists()
    assert matched == {en for entries in LABELS.values() for en, _ in entries}
    print(f"R7 {lang}: real journey; D7: {len(matched)}/36 rendered labels (4 read-only presentation controls); 0 runs; HOME empty; no external requests")


def _assert_document_toggle(page, lang):
    from playwright.sync_api import expect
    layout = page.locator(".pm-design-stack .pm-document-layout")
    _open_pm_tools(page, "revision")
    toggle = page.locator("details.pm-more-revision .pm-view-switcher button")
    source = layout.locator(".pm-source-editor")
    original = source.input_value()
    for _ in range(2):
        expect(toggle).to_have_text("소스 편집" if lang == "ko" else "Edit source")
        toggle.click()
        expect(toggle).to_have_text("문서 검토" if lang == "ko" else "Review document")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        expect(source).to_be_focused()
        expect(source).to_have_value(original)
        expect(layout.locator(".pm-preview-pane")).to_be_hidden()
        toggle.click()
        expect(toggle).to_have_text("소스 편집" if lang == "ko" else "Edit source")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        expect(source).to_be_hidden()
        expect(layout.locator(".pm-preview-pane")).to_be_visible()


def _inspect_other_guides(page, lang, matched):
    from playwright.sync_api import expect
    _tab(page, "requirements", lang)
    coverage = page.locator(".pm-coverage-entity").first
    coverage.locator("summary").click()
    expect(coverage).to_have_attribute("open", "")
    expect(_button(page, "Waive coverage", lang).first).to_be_visible()
    metadata = page.locator(".pm-card-grid > .pm-readable-card").first.locator("details.pm-requirement-meta")
    expect(metadata).to_have_count(1)
    metadata_summary = metadata.locator("summary").first
    expect(metadata_summary).to_be_visible()
    if metadata.get_attribute("open") is None:
        metadata_summary.click()
    expect(metadata).to_have_attribute("open", "")
    _guides(page, "requirements", lang, matched)
    _button(page, "Edit requirements", lang).first.click()
    _guides(page, "requirements", lang, matched)
    _tab(page, "design", lang)
    _open_pm_tools(page, "design")
    _button(page, "Storyboard", lang).click()
    page.locator(".pm-storyboard-card").first.click()
    save_storyboard = _button(page, "Save storyboard", lang)
    expect(save_storyboard).to_be_visible()
    expect(save_storyboard).to_have_js_property("tagName", "BUTTON")
    expect(save_storyboard).to_have_text(_label("Save storyboard", lang))
    matched.add("Save storyboard")
    page.get_by_role("dialog").get_by_role("button", name="취소" if lang == "ko" else "Cancel", exact=True).click()
    # The drawer renders closed help into an inert background; open it after Cancel.
    _guides(page, "design", lang, matched)
    # Storyboard replaces its surface directly, separately from UX/API.
    _button(page, "Edit source", lang).click()
    expect(_button(page, "Add storyboard", lang)).to_be_visible()
    _assert_document_toggle(page, lang)
    _guides(page, "design", lang, matched)
    # Both UX and API document subviews expose their semantic Add controls.
    for name in (["사용자 경험", "API"] if lang == "ko" else ["UX", "API"]):
        segment = page.locator(".pm-design-stack").get_by_role("tab", name=name, exact=True)
        segment.click()
        expect(segment).to_have_attribute("aria-selected", "true")
        _button(page, "Edit source", lang).click()
        expect(page.locator(".pm-design-stack .pm-document-layout")).to_be_visible()
        expect(_button(page, "Add contract" if name == "API" else "Add storyboard", lang)).to_be_visible()
        _assert_document_toggle(page, lang)
        _guides(page, "design", lang, matched)
    _tab(page, "code", lang)
    _guides(page, "code", lang, matched)


def _inspect_fixture_guides(page, lang, matched, tmp_path):
    """D7 presentation only: no waiver, worker evidence or acceptance is written."""
    from playwright.sync_api import expect
    visited = set()
    mutations = []

    def presentation(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method != "GET":
            mutations.append((request.method, path))
            route.abort()
            return
        if path.endswith("/acceptance/preview"):
            visited.add("acceptance")
            route.fulfill(json={"ready": True, "confirmation_token": "presentation-only",
                                "passing_receipt_ids": ["presentation-receipt"],
                                "missing_procedure_ids": [], "findings": []})
        elif path.endswith("/coverage"):
            response = route.fetch()
            body = response.json()
            assert body["entities"]
            body["entities"][0]["waivers"] = [{"state": "ACTIVE", "waiver_id": "presentation-waiver",
                                             "reason": "D7 presentation fixture", "reviewer": "fixture",
                                             "revision": "presentation-only"}]
            visited.add("coverage")
            route.fulfill(response=response, json=body)
        elif path.endswith("/managed-tickets"):
            response = route.fetch()
            body = response.json()
            assert body["tickets"]
            body["tickets"][0].update(head_commit="a" * 40, verified_commit="a" * 40,
                                       run_id="presentation-run", acceptance=None)
            visited.add("tickets")
            route.fulfill(response=response, json=body)
        else:
            route.fallback()

    pattern = "**/api/v1/pm/**"
    page.route(pattern, presentation)
    try:
        # Fresh navigation resets the requirement editor before fixture reads.
        page.reload()
        _open_pm_tools(page, "revision")
        expect(_button(page, "Validate", lang)).to_be_visible()
        _tab(page, "requirements", lang)
        page.locator(".pm-coverage-entity").first.locator("summary").click()
        expect(_button(page, "Revoke waiver", lang)).to_be_visible()
        _open_pm_tools(page, "change")
        _guides(page, "requirements", lang, matched)
        page.screenshot(path=str(tmp_path / f"mocked-reads-requirements-{lang}.png"), full_page=True)
        _tab(page, "code", lang)
        for name in ["Review Git commit", "Open run evidence"]:
            expect(_button(page, name, lang)).to_be_visible()
        _guides(page, "code", lang, matched)
        page.screenshot(path=str(tmp_path / f"mocked-reads-code-{lang}.png"), full_page=True)
        with page.expect_response(lambda response: "/acceptance/preview?" in response.url) as preview:
            page.get_by_role("button", name="검증 검토" if lang == "ko" else "Review verification", exact=True).first.click()
        assert preview.value.json()["ready"] is True
        expect(_button(page, "Accept verified revision", lang)).to_be_visible()
        expect(_button(page, "Accept verified revision", lang)).to_be_enabled()
        _guides(page, "code", lang, matched)
        page.screenshot(path=str(tmp_path / f"mocked-reads-acceptance-{lang}.png"), full_page=True)
        page.get_by_role("dialog").get_by_role("button", name="취소" if lang == "ko" else "Cancel", exact=True).click()
        assert visited == {"coverage", "tickets", "acceptance"}
        assert not mutations, f"Presentation fixture attempted mutations: {mutations}"
    finally:
        page.unroute(pattern, presentation)


def test_installed_demo_delayed_bootstrap_401_preserves_token_input(tmp_path, installed_python, chrome):
    """Deliver a real bootstrap 401 after typing, before submitting the token."""
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    token = "pm-demo-browser-token"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", "en"]
    pending = []

    def hold_projects(route):
        response = route.fetch()
        assert response.status == 401
        assert "authorization" not in route.request.headers
        pending.append((route, response))

    with _server(argv, tmp_path, _environment(tmp_path, SYMPHONY_API_TOKEN=token)) as (_, url), _page(chrome) as (page, _):
        page.route("**/api/v1/projects", hold_projects)
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 401) as initial:
            page.goto(url.split("#")[0] + "#/board")
        initial.value.finished()
        banner = page.locator(".api-token-banner")
        expect(banner).to_be_visible()
        field = banner.get_by_label("API token", exact=True)
        field.fill(token)
        field.focus()
        original = field.element_handle()
        assert len(pending) == 1
        route, actual = pending.pop()
        with page.expect_response(lambda response: response.url.endswith("/projects") and response.status == 401) as delayed:
            route.fulfill(response=actual)
        delayed.value.finished()
        assert delayed.value.body() == actual.body()
        # Flush the completed response's render callbacks before checking preservation.
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        expect(field).to_have_value(token)
        assert field.evaluate("(node, original) => node === original", original)
        expect(field).to_be_focused()
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") is None
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as connected:
            banner.get_by_role("button", name="Connect", exact=True).click()
        assert connected.value.request.headers["authorization"] == f"Bearer {token}"
        expect(banner).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == token
        assert _runs(directory) == 0


def test_installed_demo_invalid_token_is_rejected_then_valid_token_connects(tmp_path, installed_python, chrome):
    """A submitted wrong token still gets a real 401 and clears tab storage."""
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    token = "pm-demo-browser-token"
    wrong = "pm-demo-wrong-token"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", "en"]
    with _server(argv, tmp_path, _environment(tmp_path, SYMPHONY_API_TOKEN=token)) as (_, url), _page(chrome) as (page, _):
        with page.expect_response(lambda response: response.url.endswith("/projects") and response.status == 401) as projects, page.expect_response(lambda response: response.url.endswith("/board") and response.status == 401) as initial:
            page.goto(url.split("#")[0] + "#/board")
        projects.value.finished()
        initial.value.finished()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        banner = page.locator(".api-token-banner")
        expect(banner).to_be_visible()
        field = banner.get_by_label("API token", exact=True)
        field.fill(wrong)
        original = field.element_handle()
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 401 and response.request.headers.get("authorization") == f"Bearer {wrong}") as rejected:
            banner.get_by_role("button", name="Connect", exact=True).click()
        rejected.value.finished()
        expect(banner).to_contain_text("The stored token was rejected — enter it again.")
        expect(field).to_have_value("")
        assert field.evaluate("(node, original) => node !== original", original)
        expect(field).to_be_focused()
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") is None
        field.fill(token)
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as connected:
            field.press("Enter")
        assert connected.value.request.headers["authorization"] == f"Bearer {token}"
        expect(banner).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == token
        assert _runs(directory) == 0


def test_installed_demo_late_bootstrap_401_keeps_connected_token(tmp_path, installed_python, chrome):
    """The actual pre-Connect projects 401 must not reject a newer valid token."""
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    token = "pm-demo-browser-token"
    pending = []
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", "en"]

    def hold_projects(route):
        response = route.fetch()
        assert response.status == 401 and "authorization" not in route.request.headers
        pending.append((route, response))

    with _server(argv, tmp_path, _environment(tmp_path, SYMPHONY_API_TOKEN=token)) as (_, url), _page(chrome) as (page, _):
        page.route("**/api/v1/projects", hold_projects)
        page.goto(url.split("#")[0] + "#/board")
        banner = page.locator(".api-token-banner")
        expect(banner).to_be_visible()
        banner.get_by_label("API token", exact=True).fill(token)
        assert len(pending) == 1
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as connected:
            banner.get_by_role("button", name="Connect", exact=True).click()
        connected.value.finished()
        assert connected.value.request.headers["authorization"] == f"Bearer {token}"
        expect(banner).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == token
        route, actual = pending.pop()
        with page.expect_response(lambda response: response.url.endswith("/projects") and response.status == 401) as delayed:
            route.fulfill(response=actual)
        delayed.value.finished()
        assert delayed.value.body() == actual.body()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == token
        expect(banner).to_have_count(0)
        assert _runs(directory) == 0


@pytest.mark.parametrize("caller", ["apiRequest", "downloadRunDiagnostic", "authenticatedDownload"])
@pytest.mark.parametrize("credential", ["unauthenticated", "old-token", "current-token"])
def test_installed_demo_401_is_bound_to_request_auth(tmp_path, installed_python, chrome, caller, credential):
    """Bounded auth UI: real 401s, with controlled tab tokens and run presentation."""
    import json
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    valid = "pm-demo-valid-token-B"
    expired = "pm-demo-expired-token-A"
    request_token = None if credential == "unauthenticated" else expired
    run_id = "a" * 32
    pending = []
    downloads = []
    events = []
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", "en"]

    def set_request_token(page):
        page.evaluate("token => { if (token) sessionStorage.setItem('symphony.apiToken', token); else sessionStorage.removeItem('symphony.apiToken'); }", request_token)
        events.append({"fixture_storage": "absent" if request_token is None else "expired-A"})

    def hold_failure(route):
        response = route.fetch()
        assert response.status == 401
        assert route.request.headers.get("authorization") == (f"Bearer {request_token}" if request_token else None)
        assert response.json()["error"] == {"code": "unauthorized", "message": "missing or invalid bearer token"}
        pending.append((route, response))
        events.append({"actual_request": urlparse(route.request.url).path, "status": response.status, "request_credential": credential})
        page.evaluate("window.__authHeld401 = true")

    with _server(argv, tmp_path, _environment(tmp_path, SYMPHONY_API_TOKEN=valid)) as (_, url), _page(chrome) as (page, _):
        # Controlled fixture credentials; no alternate product token cache or hook.
        page.add_init_script("sessionStorage.setItem('symphony.apiToken', 'pm-demo-valid-token-B'); window.__authBlobCalls = 0; const originalBlobURL = URL.createObjectURL; URL.createObjectURL = function(...args) { window.__authBlobCalls++; return originalBlobURL.apply(this, args); };")
        page.on("download", lambda download: downloads.append(download.suggested_filename))
        if caller == "downloadRunDiagnostic":
            # Read-only run presentation exposes the real diagnostic button; no run exists.
            run = {"run_id": run_id, "identifier": "AUTH-FIXTURE", "title": "Auth UI presentation only", "status": "failed"}

            def present_runs(route):
                response = route.fetch()
                assert response.status == 200
                body = response.json()
                body["runs"] = [run]
                route.fulfill(response=response, json=body)

            page.route("**/api/v1/runs?*", present_runs)
            page.route(f"**/api/v1/runs/{run_id}", lambda route: route.fulfill(json={"run": run, "events": []}))
        with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as initial:
            page.goto(url.split("#")[0] + ("#/artifacts" if caller == "authenticatedDownload" else "#/runs"))
        initial.value.finished()
        assert initial.value.request.headers["authorization"] == f"Bearer {valid}"
        if caller == "authenticatedDownload":
            _tab(page, "plan", "en")
            page.get_by_label("Export format", exact=True).select_option("markdown")
            pattern = "**/api/v1/pm/exports/*/file"
            page.route(pattern, hold_failure)

            def export_then_change_credential(route):
                response = route.fetch()
                assert response.status == 201
                assert route.request.headers["authorization"] == f"Bearer {valid}"
                events.append({"actual_export_metadata_status": response.status})
                set_request_token(page)
                route.fulfill(response=response)

            page.route("**/api/v1/pm/changes/*/exports", export_then_change_credential)
            action = _button(page, "Download export", "en")
        else:
            action = page.get_by_role("button", name="Download diagnostic JSON" if caller == "downloadRunDiagnostic" else "Refresh", exact=True)
            expect(action).to_be_visible()
            if caller == "apiRequest":
                expect(page.locator("#run-attempt-list .empty-state")).to_be_visible()
            pattern = f"**/api/v1/runs/{run_id}/diagnostic" if caller == "downloadRunDiagnostic" else "**/api/v1/runs?*"
            page.route(pattern, hold_failure)
            set_request_token(page)
        with page.expect_request(pattern):
            action.click()
        page.wait_for_function("window.__authHeld401 === true")
        assert len(pending) == 1
        if credential != "current-token":
            # New B is accepted by the actual browser/server before delivering old 401.
            with page.expect_response(lambda response: response.url.endswith("/board") and response.status == 200) as restored:
                assert page.evaluate("async token => { sessionStorage.setItem('symphony.apiToken', token); return (await fetch('/api/v1/board', {headers: {Authorization: `Bearer ${token}`}})).status; }", valid) == 200
            assert restored.value.request.headers["authorization"] == f"Bearer {valid}"
            events.append({"replacement_board_status": restored.value.status, "fixture_storage": "valid-B", "expected_B_header": True})
        else:
            assert page.evaluate("sessionStorage.getItem('symphony.apiToken')") == expired
        route, actual = pending.pop()
        with page.expect_response(pattern) as rejected:
            route.fulfill(response=actual)
        rejected.value.finished()
        assert rejected.value.status == 401 and rejected.value.body() == actual.body()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        if caller == "apiRequest":
            expect(page.locator("#run-attempt-list")).to_contain_text("missing or invalid bearer token")
        elif caller == "downloadRunDiagnostic":
            expect(page.get_by_text("missing or invalid bearer token", exact=True)).to_be_visible()
        else:
            expect(page.locator(".pm-action-error")).to_contain_text("download_failed: request failed (401)")
        assert not downloads
        assert page.evaluate("window.__authBlobCalls") == 0
        stored = page.evaluate("sessionStorage.getItem('symphony.apiToken')")
        banner = page.locator(".api-token-banner")
        events.append({"actual_delivered_status": rejected.value.status, "body_unchanged": True, "stored_valid_B": stored == valid, "banner_count": banner.count(), "blob_calls": 0, "downloads": downloads})
        (tmp_path / "auth-request-events.json").write_text(json.dumps(events, indent=2) + "\n")
        if credential == "current-token":
            assert stored is None
            expect(banner).to_contain_text("The stored token was rejected — enter it again.")
            expect(banner.get_by_label("API token", exact=True)).to_have_value("")
        else:
            assert stored == valid
            expect(banner).to_have_count(0)
        assert _runs(directory) == 0


def test_ordinary_server_without_node_r15_context(tmp_path, installed_python, chrome):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    executable = str(installed_python.parent / "symphony")
    env = _environment(tmp_path)
    # Stop before pressing any button: no board tickets can dispatch normally.
    with _server([executable, "pm", "demo", "--path", str(directory), "--lang", "en"], tmp_path, env):
        assert not list((directory / "project/kanban").glob("*.md"))
    assert shutil.which("node", path="/usr/bin:/bin") is None
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?lang=en#/artifacts"
    env.update(PATH="/usr/bin:/bin", SYMPHONY_PROJECTS_FILE=str(directory / "projects.json"))
    argv = [executable, str(directory / "project/WORKFLOW.md"), "--port", str(port)]
    with _server(argv, tmp_path, env, url=url), _page(chrome) as (page, _):
        page.goto(url)
        _open_pm_tools(page, "revision")
        expect(_button(page, "Validate", "en")).to_be_visible()
        payload = page.request.get(url.split("/?")[0] + "/api/v1/pm/capabilities").json()
        assert payload["demo"] == {"active": False}
        tool = payload["host_tools"]["node"]
        reason = "Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)"
        assert tool["available"] is False and tool["reason"] == reason
        validate = _button(page, "Validate", "en")
        expect(validate).to_be_enabled()
        expect(validate).to_have_attribute("title", reason)
        approve_reason = " ".join(payload["capabilities"]["authoring"]["reasons"])
        assert approve_reason
        expect(_button(page, "Approve revision", "en")).to_be_disabled()
        expect(_button(page, "Approve revision", "en")).to_have_attribute("title", approve_reason + " " + reason)
        card = page.locator("#pm-capability-status")
        expect(card).to_contain_text("node")
        expect(card).to_contain_text(reason)
        expect(page.locator("details.pm-guide").first).not_to_have_attribute("open", "")
        with page.expect_response(lambda response: response.url.endswith("/validate") and response.request.method == "POST") as validation:
            validate.click()
        report = validation.value.json()
        assert any(item["code"] == "capability_unavailable" and item["severity"] == "blocking" for item in report["findings"])
        expect(page.locator(".pm-findings")).to_contain_text("capability_unavailable")
        expect(page.locator(".pm-findings")).to_contain_text(reason)
        page.get_by_role("button", name="Close", exact=True).click()
        _tab(page, "plan", "en")
        archive_reason = " ".join(payload["capabilities"]["handoff"]["reasons"])
        assert archive_reason
        _open_pm_tools(page, "wbs")
        expect(_button(page, "Preview archive", "en")).to_be_disabled()
        expect(_button(page, "Preview archive", "en")).to_have_attribute("title", archive_reason + " " + reason)
        assert _runs(directory) == 0
        page.screenshot(path=str(tmp_path / "ordinary-no-node.png"), full_page=True)
    assert _runs(directory) == 0


@pytest.mark.parametrize('lang', ['en', 'ko'])
def test_installed_demo_hardening_surfaces(tmp_path, installed_python, chrome, lang):
    """Actual installed CSS/geometry/modal evidence; no provider or fixture writes."""
    from playwright.sync_api import expect
    directory = tmp_path / 'demo'
    argv = [str(installed_python.parent / 'symphony'), 'pm', 'demo', '--path', str(directory), '--lang', lang]
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        page.goto(url)
        expect(page.locator('.pm-tabs')).to_be_visible()
        page.screenshot(path=str(tmp_path / f'hardening-before-{lang}.png'))
        background = page.locator('.pm-tabs').evaluate('(node) => getComputedStyle(node).backgroundColor')
        assert background not in {'rgba(0, 0, 0, 0)', 'transparent'}, background
        _tab(page, 'requirements', lang)
        expect(page.locator('.pm-readable-card').first).to_be_visible()
        for width in [320, 375, 440, 1280]:
            page.set_viewport_size({'width': width, 'height': 800 if width == 1280 else 667})
            geometry = page.locator('.pm-workspace-host').evaluate('(node) => ({client: node.clientWidth, scroll: node.scrollWidth})')
            assert geometry['scroll'] <= geometry['client'] + 1, (width, geometry)
            page.screenshot(path=str(tmp_path / f'requirements-{width}-{lang}.png'))
        # Text-size proxy only: double the actual root font; this is not browser zoom.
        page.evaluate('document.documentElement.style.fontSize = `${parseFloat(getComputedStyle(document.documentElement).fontSize) * 2}px`')
        geometry = page.locator('.pm-workspace-host').evaluate('(node) => ({client: node.clientWidth, scroll: node.scrollWidth})')
        assert geometry['scroll'] <= geometry['client'] + 1, geometry
        page.screenshot(path=str(tmp_path / f'requirements-font2x-{lang}.png'))
        page.evaluate('document.documentElement.style.fontSize = ""')
        _tab(page, 'plan', lang)
        expect(page.locator('.tabulator-row').first).to_be_visible()
        page.locator('.tabulator-row').nth(1).click()
        drawer = page.locator('.pm-task-drawer')
        expect(drawer).to_have_count(1)
        assert page.locator('.app').evaluate('(node) => node.inert'), 'Modal background must be inert'
        assert drawer.evaluate('(node) => getComputedStyle(node).backgroundColor') not in {'rgba(0, 0, 0, 0)', 'transparent'}
        drawer.get_by_role('button', name='닫기' if lang == 'ko' else 'Close', exact=True).focus()
        page.keyboard.press('Shift+Tab')
        assert drawer.evaluate('(node) => node.contains(document.activeElement)')
        page.keyboard.press('Escape')
        expect(drawer).to_have_count(0)
        assert not page.locator('.app').evaluate('(node) => node.inert')
        _tab(page, 'design', lang)
        _open_pm_tools(page, "design")
        _button(page, 'Storyboard', lang).click()
        page.locator('.pm-storyboard-card').first.click()
        drawer = page.locator('.pm-storyboard-drawer')
        expect(drawer).to_have_count(1)
        assert page.locator('.app').evaluate('(node) => node.inert')
        page.keyboard.press('Escape')
        expect(drawer).to_have_count(0)
        assert page.locator('.pm-storyboard-card').first.evaluate('(node) => node === document.activeElement')
        assert _runs(directory) == 0


@pytest.mark.parametrize('lang', ['en', 'ko'])
def test_installed_demo_hardening_drafts(tmp_path, installed_python, chrome, lang):
    from playwright.sync_api import expect
    directory = tmp_path / 'demo'
    argv = [str(installed_python.parent / 'symphony'), 'pm', 'demo', '--path', str(directory), '--lang', lang]
    discard = False
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome, confirm_response=lambda: discard) as (page, dialogs):
        page.goto(url)
        _open_pm_tools(page, "revision")
        _button(page, 'Edit source', lang).click()
        editor = page.locator('.pm-source-editor')
        original = editor.input_value()
        draft = original + '\nUnsaved recovery marker\n'
        editor.fill(draft)
        page.reload()
        _button(page, 'Edit source', lang).click()
        expect(editor).to_have_value(draft)
        expect(_button(page, 'Save draft', lang)).to_be_enabled()
        page.locator('.pm-tabs').get_by_role('button', name='설계' if lang == 'ko' else 'Design', exact=True).click()
        expect(editor).to_have_value(draft)
        assert dialogs[-1][0] == 'confirm'
        if lang == 'ko':
            assert '저장하지 않은' in dialogs[-1][1]
        discard = True
        _tab(page, 'design', lang)
        _open_pm_tools(page, "design")
        _button(page, 'Edit source', lang).click()
        _open_pm_tools(page, "revision")
        page.locator('.pm-view-switcher button').click()
        editor = page.locator('.pm-source-editor')
        ux_source = editor.input_value()
        editor.fill(ux_source + '\nUnsaved nested marker\n')
        discard = False
        page.locator('.pm-segment').filter(has_text='아키텍처' if lang == 'ko' else 'Architecture').click()
        expect(editor).to_have_value(ux_source + '\nUnsaved nested marker\n')
        discard = True
        architecture = page.locator('.pm-segment').filter(has_text='아키텍처' if lang == 'ko' else 'Architecture')
        architecture.click()
        expect(architecture).to_have_attribute('aria-selected', 'true')
        ux_tab = page.locator('.pm-segment').filter(has_text='사용자 경험' if lang == 'ko' else 'UX')
        ux_tab.click()
        expect(ux_tab).to_have_attribute('aria-selected', 'true')
        expect(page.locator('.pm-document-preview').first).to_contain_text('휴가 신청' if lang == 'ko' else 'leave')
        _open_pm_tools(page, "design")
        _button(page, 'Edit source', lang).click()
        page.locator('.pm-view-switcher button').click()
        expect(page.locator('.pm-source-editor')).to_have_value(ux_source)
        expect(_button(page, 'Save draft', lang)).to_be_disabled()
        # Dirty storyboard Escape/cancel keeps input; explicit discard closes it.
        page.locator('.pm-segment').filter(has_text='스토리보드' if lang == 'ko' else 'Storyboard').click()
        page.locator('.pm-storyboard-card').first.click()
        title = page.locator('.pm-storyboard-drawer input').first
        title.fill('Unsaved storyboard marker')
        discard = False
        page.keyboard.press('Escape')
        expect(title).to_have_value('Unsaved storyboard marker')
        expect(page.locator('.pm-storyboard-drawer')).to_have_count(1)
        assert dialogs[-1][0] == 'confirm'
        if lang == 'ko':
            assert '저장하지 않은 스토리보드' in dialogs[-1][1]
        discard = True
        page.keyboard.press('Escape')
        expect(page.locator('.pm-storyboard-drawer')).to_have_count(0)
        assert not page.locator('.app').evaluate('(node) => node.inert')
        assert _runs(directory) == 0
        page.screenshot(path=str(tmp_path / f'dirty-discard-{lang}.png'))


@pytest.mark.parametrize('lang', ['en', 'ko'])
def test_installed_demo_project_switch_has_one_localized_dirty_guard(tmp_path, installed_python, chrome, lang):
    """Read-only alternate project + refused open fixture; real browser switch handler."""
    import json
    from playwright.sync_api import expect
    directory = tmp_path / 'demo'
    argv = [str(installed_python.parent / 'symphony'), 'pm', 'demo', '--path', str(directory), '--lang', lang]
    discard = False
    opened = []
    def projects(route):
        response = route.fetch()
        body = response.json()
        other = {**body['current'], 'id': 'fixture-other-project', 'name': 'Other fixture'}
        body['projects'].append(other)
        route.fulfill(response=response, json=body)
    def refuse_open(route):
        opened.append(route.request.url)
        route.fulfill(status=409, content_type='application/json', body=json.dumps({'error': {'code': 'fixture_refusal', 'message': 'Fixture project open refused.'}}))
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome, confirm_response=lambda: discard) as (page, dialogs):
        page.route('**/api/v1/projects', projects)
        page.route('**/api/v1/projects/fixture-other-project/open', refuse_open)
        page.goto(url)
        _open_pm_tools(page, "revision")
        _button(page, 'Edit source', lang).click()
        editor = page.locator('.pm-source-editor')
        draft = editor.input_value() + '\nProject switch draft\n'
        editor.fill(draft)
        page.locator('#project-selector').select_option('fixture-other-project')
        assert len(dialogs) == 1 and dialogs[0][0] == 'confirm'
        if lang == 'ko':
            assert '저장하지 않은' in dialogs[0][1]
        expect(editor).to_have_value(draft)
        assert not opened
        discard = True
        page.locator('#project-selector').select_option('fixture-other-project')
        expect(page.get_by_text('Fixture project open refused.', exact=True)).to_be_visible()
        assert len(dialogs) == 2 and len(opened) == 1
        assert _runs(directory) == 0


@pytest.mark.parametrize('kind', ['handoffs', 'archive'])
def test_installed_demo_delayed_preview_stays_on_its_original_view(tmp_path, installed_python, chrome, kind):
    """Delayed read-only preview must not reopen an old plan on Code Files."""
    from playwright.sync_api import expect
    directory = tmp_path / 'demo'
    argv = [str(installed_python.parent / 'symphony'), 'pm', 'demo', '--path', str(directory), '--lang', 'en']
    pending = []
    writes = []
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        pattern = f'**/api/v1/pm/changes/*/{kind}/preview'
        page.route(pattern, lambda route: pending.append(route))
        page.route(f'**/api/v1/pm/changes/*/{kind}', lambda route: (writes.append(route.request.url), route.abort()))
        page.goto(url)
        _tab(page, 'plan', 'en')
        _open_pm_tools(page, "wbs")
        with page.expect_request(pattern):
            _button(page, 'Preview approved handoff' if kind == 'handoffs' else 'Preview archive', 'en').click()
        _tab(page, 'code', 'en')
        assert len(pending) == 1
        body = {'items': [], 'preview_token': 'old-view-token'} if kind == 'handoffs' else {'candidate': {'target_paths': ['old-view-archive']}, 'accepted_commits': {}, 'archive_id': 'old-view', 'confirmation_token': 'old-token'}
        with page.expect_response(pattern) as response:
            pending.pop().fulfill(status=200, json=body)
        response.value.finished()
        # Flush the completed response's render callbacks before the negative assertion.
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page.locator('[role=dialog]')).to_have_count(0)
        expect(page.locator('.pm-tabs [aria-current=page]')).to_have_text('Code Files')
        assert not page.locator('.app').evaluate('(node) => node.inert')
        assert not writes and _runs(directory) == 0
        page.screenshot(path=str(tmp_path / f'delayed-{kind}-ignored.png'))


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("oracle", ["status", "help", "child"])
def test_installed_demo_round7_ui(tmp_path, installed_python, chrome, lang, oracle):
    """Round7 user actions against a clean wheel, with server data as oracle."""
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        page.goto(url)
        _open_pm_tools(page, "revision")
        expect(_button(page, "Approve revision", lang)).to_be_visible()
        base = url.split("/?")[0]
        selected = page.locator('.pm-change-select').input_value()
        api = f"{base}/api/v1/pm/changes/{selected}"

        def get(suffix=""):
            response = page.request.get(api + suffix, params={"project_id": "pm-demo"})
            assert response.status == 200, response.text()
            return response.json()

        def status_matches():
            detail = get()
            expect(page.locator('.pm-change-select')).to_have_value(selected)
            expect(page.locator('.pm-change-select option:checked')).to_have_text(f"{detail['locator']} · {_planning_state_label(detail['state'], lang)}")
            expect(page.locator('.pm-revision-bar .pm-state')).to_have_text(_planning_state_label(detail['state'], lang))

        if oracle == "status":
            status_matches()
            with page.expect_response(lambda response: response.url.endswith('/approvals') and response.request.method == 'POST') as approved:
                _button(page, "Approve revision", lang).click()
            assert approved.value.status == 201
            expect(page.locator('.pm-revision-bar .pm-state')).to_have_text(_planning_state_label('Approved', lang))
            status_matches()
            page.reload()
            expect(page.locator('.pm-revision-bar .pm-state')).to_have_text(_planning_state_label('Approved', lang))
            status_matches()
            _open_pm_tools(page, "revision")
            _button(page, "Edit source", lang).click()
            editor = page.locator('.pm-source-editor')
            original = editor.input_value()
            edited = original.replace('## Problem\n', '## Problem\nRound7 regression.\n', 1)
            assert edited != original
            editor.fill(edited)
            with page.expect_response(lambda response: '/documents/proposal' in response.url and response.request.method == 'PATCH') as saved:
                _button(page, "Save draft", lang).click()
            assert saved.value.status == 200
            expect(page.locator('.toast-success').last).to_be_visible()
            status_matches()
            page.reload()
            _open_pm_tools(page, "revision")
            expect(_button(page, "Edit source", lang)).to_be_visible()
            status_matches()
            _button(page, "Edit source", lang).click()
            expect(page.locator('.pm-source-editor')).to_have_value(edited)
            page.locator('.pm-source-editor').fill(original)
            with page.expect_response(lambda response: '/documents/proposal' in response.url and response.request.method == 'PATCH') as restored:
                _button(page, "Save draft", lang).click()
            assert restored.value.status == 200
            expect(page.locator('.toast-success').last).to_be_visible()
            status_matches()
            assert (directory / 'planning/openspec/changes/team-leave-request/proposal.md').read_text() == original
        elif oracle == "help":
            matched = set()
            for width, height in [(1440, 1000), (1480, 706)]:
                page.set_viewport_size({"width": width, "height": height})
                page.reload()
                expect(page.locator('details.pm-guide')).to_have_count(2)
                for guide in page.locator('details.pm-guide').all():
                    expect(guide).to_have_js_property('open', False)
                page.screenshot(path=str(tmp_path / f'round7-entry-{lang}-{width}x{height}.png'))
            _open_pm_tools(page, "change")
            _open_pm_tools(page, "revision")
            _guides(page, 'idea', lang, matched)
            _guides(page, 'idea', lang, matched)  # Reuse must tolerate already-open details.
            for area in ['requirements', 'design', 'plan', 'code']:
                _tab(page, area, lang)
                for guide in page.locator('details.pm-guide').all():
                    expect(guide).to_have_js_property('open', False)
                _guides(page, area, lang, matched)
        else:
            _tab(page, 'plan', lang)
            rows = page.locator('.pm-tabulator .tabulator-row')
            expect(rows).to_have_count(7)
            tasks = get('/wbs')['tasks']
            group = next(task['task_id'] for task in tasks if not task['executable'])
            leaf = next(task['task_id'] for task in tasks if task['executable'])
            _open_pm_tools(page, "wbs")
            child = _button(page, 'Add child', lang)
            hint = page.locator('#pm-add-child-hint')

            def select(task_id, invalid):
                rows.filter(has=page.locator(f'[tabulator-field="task_id"]:text-is("{task_id}")')).click()
                page.keyboard.press('Escape')
                if invalid:
                    expect(child).to_be_disabled()
                    expect(hint).to_be_visible()
                    expect(hint).to_have_text('하위 작업을 추가하려면 그룹을 선택하세요.' if lang == 'ko' else 'Select a group to add a child task.')
                    expect(child).to_have_attribute('aria-describedby', 'pm-add-child-hint')
                else:
                    expect(child).to_be_enabled()
                    expect(hint).to_be_hidden()
                    assert child.get_attribute('aria-describedby') is None

            # No selected task means a real root create; Undo must restore every object.
            expect(child).to_be_enabled()
            with page.expect_response(lambda response: response.url.endswith('/wbs') and response.request.method == 'PATCH') as root_added:
                child.click()
            assert root_added.value.status == 200
            assert root_added.value.request.post_data_json['payload']['operations'][0]['task']['parent_id'] is None
            expect(rows).to_have_count(8)
            with page.expect_response(lambda response: response.url.endswith('/wbs') and response.request.method == 'PATCH'):
                _button(page, 'Undo', lang).click()
            expect(rows).to_have_count(7)
            assert get('/wbs')['tasks'] == tasks
            select(leaf, True)
            select(group, False)
            select(leaf, True)
            page.reload()
            expect(rows).to_have_count(7)
            _open_pm_tools(page, "wbs")
            expect(child).to_be_disabled()  # Restored selection, before any new row click.
            expect(hint).to_be_visible()
            select(group, False)
            with page.expect_response(lambda response: response.url.endswith('/wbs') and response.request.method == 'PATCH') as added:
                child.click()
            assert added.value.status == 200
            assert added.value.request.post_data_json['payload']['operations'][0]['task']['parent_id'] == group
            expect(rows).to_have_count(8)
            with page.expect_response(lambda response: response.url.endswith('/wbs') and response.request.method == 'PATCH'):
                _button(page, 'Undo', lang).click()
            expect(rows).to_have_count(7)
            assert get('/wbs')['tasks'] == tasks
            # UI affordance does not replace server authority or rewrite stale IDs.
            for parent, finding in [(leaf, 'invalid_parent'), ('T-missing-round7', 'missing_parent')]:
                before = get()
                response = page.request.patch(api + '/wbs', data={
                    'project_id': 'pm-demo', 'change_id': selected, 'expected_revision': before['revision'],
                    'client_operation_id': f'round7-{parent}', 'payload': {'operations': [{'op': 'create_task', 'task': {
                        'parent_id': parent, 'order': 999, 'kind': 'Feature', 'title': 'Refused child', 'executable': True,
                    }}]},
                })
                assert response.status == 409, response.text()
                assert finding in response.text()
                assert get('/wbs')['tasks'] == tasks
                assert get()['revision'] == before['revision']
            page.screenshot(path=str(tmp_path / f'round7-child-{lang}.png'))
        assert _runs(directory) == 0
        assert not list((directory / 'project/kanban').glob('*.md'))


def _round5_contrast(locator):
    """Read the rendered solid text/background pair, including transparent parents."""
    return locator.evaluate(r"""node => {
      const rgb = value => value.match(/[\d.]+/g).map(Number);
      const foreground = rgb(getComputedStyle(node).color);
      let surface = node, background;
      while (surface) {
        background = rgb(getComputedStyle(surface).backgroundColor);
        if (background.length < 4 || background[3] === 1) break;
        if (background[3] !== 0) throw new Error('Contrast oracle requires a solid background');
        surface = surface.parentElement;
      }
      if (!surface || (foreground.length === 4 && foreground[3] !== 1)) throw new Error('No solid rendered pair');
      const luminance = channels => channels.slice(0, 3).map(value => {
        value /= 255; return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
      const a = luminance(foreground), b = luminance(background);
      return {foreground, background, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05)};
    }""")


def _round5_table(table):
    """Check actual line boxes and reach the final column through its local scroller."""
    return table.evaluate("""table => {
      const wrapper = table.parentElement;
      const headings = [...table.querySelectorAll('th')].map(th => {
        const range = document.createRange(); range.selectNodeContents(th);
        return {text: th.textContent, lines: range.getClientRects().length};
      });
      const heading = table.querySelector('th:last-child');
      const bounds = wrapper.getBoundingClientRect();
      wrapper.scrollLeft += heading.getBoundingClientRect().left - bounds.left;
      const range = document.createRange(); range.selectNodeContents(heading);
      const last = range.getBoundingClientRect();
      return {headings, clientWidth: wrapper.clientWidth, scrollWidth: wrapper.scrollWidth, scrollLeft: wrapper.scrollLeft,
        lastReachable: last.right <= bounds.right + 1 && last.left >= bounds.left - 1};
    }""")


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("oracle", ["plan", "tables", "contrast"])
def test_installed_demo_round5_rendered_ui(tmp_path, installed_python, chrome, lang, oracle):
    """Independent oracles keep each of the three rendered defects visible in red proof."""
    import json
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    receipts = []
    try:
        with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
            page.goto(url)
            _open_pm_tools(page, "revision")
            expect(_button(page, "Validate", lang)).to_be_visible()
            if oracle == 'tables':
                _button(page, 'Approve revision', lang).click()
                expect(page.locator('.pm-revision-bar .pm-state')).to_have_text(_planning_state_label('Approved', lang))
                _tab(page, 'plan', lang)
                _open_pm_tools(page, "wbs")
                _button(page, 'Preview approved handoff', lang).click()
                with page.expect_response(lambda response: response.url.endswith('/handoffs') and response.request.method == 'POST') as handed_off:
                    page.get_by_role('button', name='네이티브 티켓 생성' if lang == 'ko' else 'Create native tickets', exact=True).click()
                assert handed_off.value.status == 201
            for width, height in [(320, 568), (375, 667), (440, 956), (1280, 800)]:
                page.set_viewport_size({"width": width, "height": height})
                if oracle == "plan":
                    _tab(page, "plan", lang)
                    _open_pm_tools(page, "wbs")
                    host = page.locator('.pm-workspace-host').bounding_box()
                    actions = page.locator('.pm-plan-action-row button, .pm-plan-toolbar button')
                    assert actions.count() >= 10
                    for action in actions.all():
                        box = action.bounding_box()
                        receipts.append({"width": width, "button": action.inner_text(), "box": box, "host": host})
                        assert box and box['x'] >= host['x'] - 1 and box['x'] + box['width'] <= host['x'] + host['width'] + 1, receipts[-1]
                else:
                    _tab(page, "requirements", lang)
                    coverage = page.locator('.pm-coverage-entity').first
                    if coverage.get_attribute('open') is None:
                        coverage.locator('summary').click()
                    expect(page.locator('.pm-coverage-state.ready').first).to_be_visible()
                    if oracle == "contrast":
                        pair = _round5_contrast(page.locator('.pm-coverage-state.ready').first)
                        receipts.append({"width": width, "coverage": pair})
                        assert pair['ratio'] >= 4.5, receipts[-1]
                    else:
                        # Coverage task links, dependencies, code delta and planning files.
                        tables = [page.locator('.pm-coverage-entity[open] .pm-compact-table').first]
                        for table in tables:
                            result = _round5_table(table)
                            receipts.append({"width": width, **result})
                            assert all(item['lines'] == 1 for item in result['headings']), receipts[-1]
                            assert result['lastReachable'], receipts[-1]
                        _tab(page, 'plan', lang)
                        _button(page, 'Dependencies', lang).click()
                        dependency_table = page.locator('.pm-compact-table')
                        scroller = dependency_table.locator('..')
                        if scroller.evaluate('node => node.scrollWidth > node.clientWidth'):
                            # Start from the preceding real control and reach the overflow with Tab.
                            page.locator('.pm-segmented button').last.focus()
                            page.keyboard.press('Tab')
                            expect(scroller).to_be_focused()
                            scroller.evaluate('node => { node.scrollLeft = 0; }')
                            page.keyboard.press('ArrowRight')
                            page.wait_for_function('node => node.scrollLeft > 0', arg=scroller.element_handle())
                            receipts.append({"width": width, "keyboardLocalScroll": scroller.evaluate('node => node.scrollLeft'), "pageScrollX": page.evaluate('scrollX')})
                            assert page.evaluate('scrollX') == 0
                        result = _round5_table(dependency_table)
                        receipts.append({"width": width, **result})
                        assert all(item['lines'] == 1 for item in result['headings']) and result['lastReachable'], receipts[-1]
                        _button(page, 'Work breakdown', lang).click()
                        rows = page.locator('.pm-tabulator .tabulator-row')
                        expect(rows).to_have_count(7)
                        task_id = rows.nth(1).locator('[tabulator-field="task_id"]').inner_text()
                        holder = page.locator('.pm-tabulator .tabulator-tableholder')
                        holder.focus()
                        with page.expect_response(lambda response: response.url.endswith('/wbs/preview')) as preview:
                            holder.evaluate("""(node, id) => {
                                const data = new DataTransfer(); data.setData('text/plain', `${id}\ttitle\tRound5 preview only`);
                                node.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, clipboardData: data}));
                            }""", task_id)
                        assert preview.value.status == 200
                        dialog = page.get_by_role('dialog')
                        expect(dialog).to_be_visible()
                        result = _round5_table(dialog.locator('.pm-compact-table'))
                        receipts.append({"width": width, "dialog": "TSV preview", **result})
                        assert all(item['lines'] == 1 for item in result['headings']) and result['lastReachable'], receipts[-1]
                        page.keyboard.press('Escape')
                        expect(dialog).to_have_count(0)
                        _tab(page, 'code', lang)
                        for detail in page.locator('details').all():
                            detail.evaluate("node => { node.open = true; }")
                        tables = page.locator('.pm-ticket-card .pm-compact-table, .pm-readable-card > .pm-table-scroll > .pm-compact-table')
                        assert tables.count() == 6
                        for table in tables.all():
                            result = _round5_table(table)
                            receipts.append({"width": width, **result})
                            assert all(item['lines'] == 1 for item in result['headings']) and result['lastReachable'], receipts[-1]
                page.screenshot(path=str(tmp_path / f'{oracle}-{lang}-{width}.png'), full_page=True)
            assert _runs(directory) == 0
    finally:
        (tmp_path / 'round5-rendered.json').write_text(json.dumps(receipts, indent=2), encoding='utf-8')


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("oracle", ["feedback", "guidance"])
def test_installed_demo_round5_archive_failure(tmp_path, installed_python, chrome, lang, oracle):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        page.goto(url)
        _tab(page, 'plan', lang)
        _open_pm_tools(page, "wbs")
        with page.expect_response(lambda response: response.url.endswith('/archive/preview')) as refusal:
            _button(page, 'Preview archive', lang).click()
        assert refusal.value.status == 409
        assert refusal.value.json()['error'] == {'code': 'approval_required', 'message': 'archive requires the approved revision', 'details': {}}
        alert = page.locator('.pm-action-error')
        expect(alert).to_have_attribute('role', 'alert')
        expected = ('승인된 리비전이 있어야 아카이브할 수 있습니다. 현재 리비전을 검토하고 승인한 뒤 아카이브 미리보기를 다시 여세요.' if lang == 'ko' else
                    'Archive requires an approved revision. Review and approve this revision, then preview archive again.')
        if oracle == 'guidance':
            expect(alert).to_contain_text('approval_required: ' + expected)
        toast = page.locator('#toast-container .toast').last
        expect(toast).to_have_class(re.compile(r'\btoast-error\b'))
        expect(toast).not_to_have_attribute('role', re.compile('alert|status'))
        expect(toast).not_to_have_attribute('aria-live', re.compile('polite|assertive'))
        # One persistent announcer, with the same message; no duplicate live toast.
        expect(page.locator('[role=alert]').filter(has_text='approval_required:')).to_have_count(1)
        expect(_button(page, 'Preview archive', lang)).to_be_focused()
        assert _round5_contrast(toast)['ratio'] >= 4.5
        page.screenshot(path=str(tmp_path / f'archive-{lang}.png'), full_page=True)
        assert _runs(directory) == 0


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("oracle", ["contrast", "guidance"])
def test_installed_demo_round5_problem_recovery(tmp_path, installed_python, chrome, lang, oracle):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        page.goto(url)
        _open_pm_tools(page, "revision")
        _button(page, 'Edit source', lang).click()
        editor = page.locator('.pm-source-editor')
        original = editor.input_value()
        invalid = re.sub(r'(?m)^## Problem$', '## Missing problem heading', original)
        assert invalid != original
        identities = re.findall(r'<!-- symphony:[^>]+-->', original)
        assert identities and re.findall(r'<!-- symphony:[^>]+-->', invalid) == identities
        editor.fill(invalid)
        with page.expect_response(lambda response: '/documents/proposal' in response.url and response.request.method == 'PATCH') as saved:
            _button(page, 'Save draft', lang).click()
        assert saved.value.status == 200
        toast = page.locator('.toast-success').last
        expect(toast).to_be_visible()
        if oracle == 'contrast':
            assert _round5_contrast(toast)['ratio'] >= 4.5
        with page.expect_response(lambda response: response.url.endswith('/validate')) as validation:
            _button(page, 'Validate', lang).click()
        report = validation.value.json()
        assert validation.value.status == 200 and report['valid'] is False
        finding = next(item for item in report['findings'] if item['code'] == 'idea_field_required' and item['field'] == 'problem')
        assert finding['message'] == 'Idea field problem is required'
        dialog = page.get_by_role('dialog')
        expect(dialog).to_contain_text('문제 항목이 빠져 있습니다. 아이디어 소스에 문제 항목을 추가하고 저장한 뒤 다시 검증하세요.' if lang == 'ko' else
                                     'The Problem field is missing. Add it to the idea source, save, then validate again.')
        expect(dialog).to_contain_text('idea_field_required')
        page.keyboard.press('Escape')
        expect(_button(page, 'Validate', lang)).to_be_focused()
        _button(page, 'Edit source', lang).click() if page.locator('.pm-source-editor').is_hidden() else None
        editor.fill(original)
        with page.expect_response(lambda response: '/documents/proposal' in response.url and response.request.method == 'PATCH') as restored:
            _button(page, 'Save draft', lang).click()
        assert restored.value.status == 200
        with page.expect_response(lambda response: response.url.endswith('/validate')) as validation:
            _button(page, 'Validate', lang).click()
        assert validation.value.status == 200 and validation.value.json()['valid'] is True
        assert (directory / 'planning/openspec/changes/team-leave-request/proposal.md').read_text() == original
        assert _runs(directory) == 0


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_installed_demo_round6_overlay_table_keyboard(tmp_path, installed_python, chrome, lang):
    """A real TSV preview's local overflow must participate in modal Tab order."""
    import json
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    receipt = {"language": lang, "tab_path": []}
    try:
        with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
            page.set_viewport_size({"width": 320, "height": 568})
            page.goto(url)
            _tab(page, 'plan', lang)
            rows = page.locator('.pm-tabulator .tabulator-row')
            expect(rows).to_have_count(7)
            task_id = rows.nth(1).locator('[tabulator-field="task_id"]').inner_text()
            source = (directory / 'planning/openspec/changes/team-leave-request/tasks.md').read_bytes()
            holder = page.locator('.pm-tabulator .tabulator-tableholder')
            holder.focus()
            with page.expect_response(lambda response: response.url.endswith('/wbs/preview')) as response:
                holder.evaluate("""(node, id) => {
                    const data = new DataTransfer(); data.setData('text/plain', `${id}\tnotes\tRound6 preview keeps the planning source unchanged`);
                    node.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, clipboardData: data}));
                }""", task_id)
            assert response.value.status == 200
            receipt['preview_status'] = response.value.status
            receipt['preview_body'] = response.value.json()
            dialog = page.get_by_role('dialog')
            expect(dialog).to_be_visible()
            scroller = dialog.locator('.pm-table-scroll')
            receipt['dimensions'] = scroller.evaluate('node => ({clientWidth: node.clientWidth, scrollWidth: node.scrollWidth})')
            assert receipt['dimensions']['scrollWidth'] > receipt['dimensions']['clientWidth']
            for _ in range(12):
                receipt['tab_path'].append(page.evaluate("() => ({tag: document.activeElement.tagName, text: document.activeElement.textContent, className: document.activeElement.className})"))
                if scroller.evaluate('node => node === document.activeElement'):
                    break
                page.keyboard.press('Tab')
            receipt['reached_by_tab'] = scroller.evaluate('node => node === document.activeElement')
            assert receipt['reached_by_tab'], receipt
            expect(scroller).to_have_attribute('tabindex', '0')
            expect(scroller).to_have_attribute('role', 'region')
            assert scroller.get_attribute('aria-label')
            receipt['focus_style'] = scroller.evaluate("node => ({style: getComputedStyle(node).outlineStyle, width: getComputedStyle(node).outlineWidth, color: getComputedStyle(node).outlineColor})")
            assert receipt['focus_style']['style'] != 'none'
            assert float(receipt['focus_style']['width'].removesuffix('px')) >= 2
            page.screenshot(path=str(tmp_path / f'overlay-scroller-focus-{lang}.png'))
            # The scroller is before Apply/Cancel in document order; both trap edges stay contained.
            page.keyboard.press('Shift+Tab')
            expect(dialog.locator('.pm-actions button').last).to_be_focused()
            page.keyboard.press('Tab')
            expect(scroller).to_be_focused()
            page.keyboard.press('ArrowRight')
            page.wait_for_function('node => node.scrollLeft > 0', arg=scroller.element_handle())
            receipt['keyboard_scroll_left'] = scroller.evaluate('node => node.scrollLeft')
            receipt['page_scroll_x'] = page.evaluate('scrollX')
            assert receipt['page_scroll_x'] == 0
            page.keyboard.press('Tab')
            expect(dialog.locator('.pm-actions button').first).to_be_focused()
            page.keyboard.press('Tab')
            expect(dialog.locator('.pm-actions button').last).to_be_focused()
            page.keyboard.press('Tab')
            expect(scroller).to_be_focused()
            page.keyboard.press('Escape')
            expect(dialog).to_have_count(0)
            expect(holder).to_be_focused()
            assert not page.locator('.app').evaluate('node => node.inert')
            assert (directory / 'planning/openspec/changes/team-leave-request/tasks.md').read_bytes() == source
            assert _runs(directory) == 0
    finally:
        (tmp_path / 'round6-overlay-keyboard.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')


def test_installed_round6_reserved_ids_overlay_component(tmp_path, installed_python, chrome):
    """Shared installed overlay/CSS component fixture; this does not simulate a provider result."""
    import json
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", "en"]
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url), _page(chrome) as (page, _):
        page.set_viewport_size({"width": 320, "height": 568})
        page.goto(url)
        _open_pm_tools(page, "revision")
        expect(_button(page, 'Validate', 'en')).to_be_visible()
        opener = _button(page, 'Validate', 'en')
        opener.focus()
        page.evaluate("""async () => {
            const {createPmOverlay} = await import('/static/pm/overlay.js');
            const make = (tag, className, text) => {
                const node = document.createElement(tag); node.className = className || '';
                if (text) node.textContent = text; return node;
            };
            const owner = document.querySelector('.pm-workspace-host');
            const panel = make('section', 'pm-inline-dialog'); panel.setAttribute('role', 'dialog');
            panel.setAttribute('aria-label', 'Reserved IDs component fixture');
            const section = make('section', 'pm-proposal-ids');
            section.append(make('h3', '', 'Reserved permanent identities'));
            const wrapper = make('div', 'pm-table-scroll'), table = make('table', 'pm-compact-table');
            const head = make('thead'), headings = make('tr');
            for (const title of ['Placeholder', 'Permanent ID']) {
                const cell = make('th', '', title); cell.setAttribute('scope', 'col'); headings.append(cell);
            }
            head.append(headings); const body = make('tbody'), row = make('tr');
            row.append(make('td', '', '$requirement_for_team_leave_request_form'), make('td', '', 'REQ-12345678'));
            body.append(row); table.append(head, body); wrapper.append(table); section.append(wrapper);
            const actions = make('div', 'pm-actions'), apply = make('button', 'btn', 'Apply component fixture');
            actions.append(apply, make('button', 'btn', 'Discard component fixture')); panel.append(section, actions);
            window.__round6ComponentOverlay = createPmOverlay(owner);
            window.__round6ComponentOverlay.open(panel, {initialFocus: apply});
        }""")
        dialog = page.get_by_role('dialog', name='Reserved IDs component fixture', exact=True)
        scroller = dialog.locator('.pm-table-scroll')
        assert scroller.evaluate('node => node.scrollWidth > node.clientWidth')
        page.keyboard.press('Tab')
        expect(dialog.get_by_role('button', name='Discard component fixture', exact=True)).to_be_focused()
        page.keyboard.press('Tab')
        expect(scroller).to_be_focused()
        expect(scroller).to_have_attribute('aria-label', 'Placeholder, Permanent ID')
        focus = scroller.evaluate("node => ({style: getComputedStyle(node).outlineStyle, width: getComputedStyle(node).outlineWidth})")
        assert focus['style'] != 'none' and float(focus['width'].removesuffix('px')) >= 2
        page.keyboard.press('ArrowRight')
        page.wait_for_function('node => node.scrollLeft > 0', arg=scroller.element_handle())
        assert page.evaluate('scrollX') == 0
        page.screenshot(path=str(tmp_path / 'reserved-ids-component-focus.png'))
        # A fitting table must leave the modal tab sequence after a real viewport resize.
        page.set_viewport_size({"width": 1280, "height": 800})
        assert scroller.evaluate('node => node.scrollWidth <= node.clientWidth')
        dialog.get_by_role('button', name='Discard component fixture', exact=True).focus()
        page.keyboard.press('Tab')
        expect(dialog.get_by_role('button', name='Apply component fixture', exact=True)).to_be_focused()
        expect(scroller).to_have_attribute('tabindex', '-1')
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        expect(opener).to_be_focused()
        page.evaluate('window.__round6ComponentOverlay.destroy()')
        assert _runs(directory) == 0
        (tmp_path / 'reserved-ids-component.json').write_text(json.dumps({"kind": "real installed overlay and CSS with DOM component fixture", "provider_result": False, "focus": focus, "keyboard_local_scroll": True, "fitting_resize_excluded": True}, indent=2), encoding='utf-8')


@pytest.fixture(params=["headless-default", "native-scrollbars"])
def geometry_chrome(request, chrome):
    """Geometry-only native scrollbar variant; original Chrome fixture is unchanged."""
    if request.param == "headless-default":
        yield chrome, request.param
        return
    browser = chrome.browser_type.launch(
        channel="chrome", headless=True,
        ignore_default_args=["--hide-scrollbars"],
    )
    try:
        yield browser, request.param
    finally:
        browser.close()


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("width,height", [(320, 568), (375, 667), (440, 956), (1280, 800), (1280, 706)])
def test_installed_demo_initial_artifact_geometry(tmp_path, installed_python, geometry_chrome, lang, width, height):
    """Initial complete authored content; measured before raw viewport screenshots."""
    chrome, scrollbar_mode = geometry_chrome
    import hashlib
    import json
    import struct

    from playwright.sync_api import expect

    # Pinned first authored paragraphs from the unchanged bilingual demo resources.
    first_paragraph = {
        "en": {
            "idea": "Employees ask for leave in chat, and managers lose track of requests. Teammates cannot reliably see who will be away.",
            "requirements": "The system SHALL store a valid leave request as Pending and MUST refuse dates that overlap the same employee's Pending or Approved requests. The start date MUST be on or before the end date; both dates are included.",
            "design": "An employee submits leave dates. Their team manager decides the request. Teammates check approved leave before planning work.",
        },
        "ko": {
            "idea": "직원들이 메신저로 휴가를 신청하면 관리자는 요청을 놓치기 쉽습니다. 팀원들도 누가 언제 자리를 비우는지 정확히 확인하기 어렵습니다.",
            "requirements": "시스템은 유효한 휴가 신청을 Pending(승인 대기)으로 저장해야 합니다(SHALL). 같은 직원의 Pending(승인 대기) 또는 Approved(승인) 신청과 날짜가 겹치면 반드시 거절해야 합니다(MUST). 시작일은 종료일보다 늦을 수 없으며, 시작일과 종료일을 모두 휴가에 포함해야 합니다(MUST).",
            "design": "직원이 휴가 날짜를 신청하면 소속 팀의 관리자가 처리합니다. 팀원들은 업무를 계획하기 전에 승인된 휴가를 확인합니다.",
        },
    }
    measure = r"""({area}) => {
      const host = document.querySelector('.pm-workspace-host');
      const nav = host.querySelector('.pm-tabs');
      const closed = node => {
        for (let parent = node.parentElement; parent; parent = parent.parentElement) {
          if (parent.tagName === 'DETAILS' && !parent.open) {
            const summary = Array.from(parent.children).find(child => child.tagName === 'SUMMARY');
            if (!summary || !summary.contains(node)) return true;
          }
        }
        return false;
      };
      const rect = node => {
        if (!node) return null;
        const r = node.getBoundingClientRect();
        return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height};
      };
      const suppressed = node => {
        const reasons = [];
        for (let owner = node; owner; owner = owner.parentElement) {
          const style = getComputedStyle(owner);
          if (owner.hidden || owner.getAttribute('aria-hidden') === 'true'
              || style.display === 'none' || style.visibility !== 'visible'
              || Number(style.opacity) === 0 || style.contentVisibility === 'hidden') reasons.push(owner.tagName);
          if (style.webkitLineClamp && style.webkitLineClamp !== 'none' && style.webkitLineClamp !== '0') reasons.push('line-clamp');
          if (style.textOverflow === 'ellipsis') reasons.push('ellipsis');
        }
        if (!node.getClientRects().length) reasons.push('no-layout');
        return reasons;
      };
      const clipped = (node, horizontal) => {
        const r = node.getBoundingClientRect(), failures = [];
        for (let owner = node.parentElement; owner; owner = owner.parentElement) {
          const s = getComputedStyle(owner), b = owner.getBoundingClientRect();
          const top = b.top + owner.clientTop, left = b.left + owner.clientLeft;
          if (/(auto|scroll|hidden|clip)/.test(s.overflowY)
              && (r.top < top - 1 || r.bottom > top + owner.clientHeight + 1)) failures.push('vertical:' + owner.className);
          if (horizontal && /(auto|scroll|hidden|clip)/.test(s.overflowX)
              && (r.left < left - 1 || r.right > left + owner.clientWidth + 1)) failures.push('horizontal:' + owner.className);
        }
        return failures;
      };
      const info = node => node && ({
        tag:node.tagName, text:node.textContent.trim(), rect:rect(node),
        font:Number.parseFloat(getComputedStyle(node).fontSize),
        closed:closed(node), suppressed:suppressed(node), clipped:clipped(node, area !== 'plan' && area !== 'code'),
      });
      const target = node => {
        const r = rect(node), off = r.right <= 0 || r.left >= innerWidth || r.bottom <= 0 || r.top >= innerHeight;
        const full = !off && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight && !clipped(node, true).length;
        const owned = (x,y) => { const hit = document.elementFromPoint(x,y); return hit === node || node.contains(hit); };
        return {...info(node), visibility:off ? 'outside' : full ? 'complete' : 'partial',
          hit:full ? [[r.left+1,(r.top+r.bottom)/2],[r.right-1,(r.top+r.bottom)/2],[(r.left+r.right)/2,r.top+1],[(r.left+r.right)/2,r.bottom-1],[(r.left+r.right)/2,(r.top+r.bottom)/2]].every(([x,y]) => owned(x,y)) : null};
      };
      const articles = Array.from(host.querySelectorAll('.pm-document-preview')).filter(node => !closed(node) && !node.closest('.pm-baseline'));
      const article = articles[0];
      const first = area === 'plan' ? host.querySelector('.pm-tabulator .tabulator-row')
        : area === 'code' ? host.querySelector('.pm-readable-card .pm-compact-table tbody tr')
        : article?.querySelector('p');
      const heading = article?.querySelector('h1') || article?.querySelector('h2') || article?.querySelector('h3,h4');
      const authored = Array.from(host.querySelectorAll('.pm-document-preview h1,.pm-document-preview h2,.pm-document-preview h3,.pm-document-preview h4,.pm-document-preview p,.pm-document-preview li,.pm-document-preview blockquote,.pm-document-preview pre')).filter(node => !closed(node));
      const hostStyle = getComputedStyle(host);
      const borderWidth = (Number.parseFloat(hostStyle.borderLeftWidth) || 0) + (Number.parseFloat(hostStyle.borderRightWidth) || 0);
      return {
        timestamp:new Date().toISOString(), url:location.href, lang:window.i18n.lang,
        viewport:{width:innerWidth,height:innerHeight,devicePixelRatio},
        scroll:{x:scrollX,y:scrollY,documentTop:document.scrollingElement.scrollTop,
          documentLeft:document.scrollingElement.scrollLeft,hostTop:host.scrollTop,hostLeft:host.scrollLeft},
        overflow:{page:document.documentElement.scrollWidth-document.documentElement.clientWidth,host:host.scrollWidth-host.clientWidth},
        scrollbar:{offsetWidth:host.offsetWidth, clientWidth:host.clientWidth, borderWidth,
          width:host.offsetWidth-host.clientWidth-borderWidth,
          scrollHeight:host.scrollHeight, clientHeight:host.clientHeight},
        host:rect(host), nav:rect(nav), selected:Array.from(nav.querySelectorAll('[aria-current="page"]')).map(info),
        first:info(first), heading:info(heading), authored:authored.map(info),
        tabs:Array.from(nav.querySelectorAll('button')).map(target),
        coverage:Array.from(host.querySelectorAll('details.pm-coverage-entity > summary')).filter(node => !closed(node)).map(target),
        enhancedRows:host.querySelectorAll('.pm-tabulator .tabulator-row').length,
        codeRows:host.querySelectorAll('.pm-readable-card .pm-compact-table tbody tr').length,
        nonNativeButtons:Array.from(host.querySelectorAll('[role="button"]:not(button)')).map(info),
      };
    }"""
    directory = tmp_path / "demo"
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    failures = []
    with _server(argv, tmp_path, _environment(tmp_path)) as (_, url):
        for area in TABS:
            # Fresh context prevents disclosure, subview and scroll state from leaking between cells.
            with _page(chrome) as (page, _):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(url)
                expect(page.locator(".pm-tabs")).to_be_visible()
                _tab(page, area, lang)
                first = page.locator(".pm-tabulator .tabulator-row").first if area == "plan" else (
                    page.locator(".pm-readable-card .pm-compact-table tbody tr").first if area == "code"
                    else page.locator(".pm-document-preview").first.locator("p").first)
                # Hydrated real content, not a loading card or a later, shorter visible paragraph.
                if area in first_paragraph[lang]:
                    expect(first).to_have_text(first_paragraph[lang][area])
                else:
                    expect(first).to_contain_text(re.compile(r"\S"))
                if area == "plan":
                    expect(page.locator(".pm-tabulator .tabulator-row")).to_have_count(7)
                page.evaluate("document.fonts.ready")
                stem = f"initial-{scrollbar_mode}-{area}-{width}x{height}-{lang}"
                png = tmp_path / f"{stem}.png"
                receipt = tmp_path / f"{stem}.json"
                before = page.evaluate(measure, {"area": area})
                evidence = {"before": before, "screenshot": str(png), "wheel": os.environ.get("SYMPHONY_PM_DEMO_WHEEL"), "scrollbar_mode": scrollbar_mode}
                # Persist measurement first so a screenshot failure cannot erase the geometry.
                receipt.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                page.screenshot(path=str(png), full_page=False)
                after = page.evaluate(measure, {"area": area})
                raw = png.read_bytes()
                evidence.update(after=after, png_sha256=hashlib.sha256(raw).hexdigest(),
                                png_dimensions=list(struct.unpack(">II", raw[16:24])))
                receipt.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
                    assert evidence["png_dimensions"] == [width, height]
                    assert before["viewport"] == {"width": width, "height": height, "devicePixelRatio": 1}
                    assert before["lang"] == lang
                    scrollbar = before["scrollbar"]
                    if scrollbar_mode == "native-scrollbars" and scrollbar["scrollHeight"] > scrollbar["clientHeight"] + 1:
                        assert scrollbar["width"] > 0, ("Native scrollbar admission failed; no reserved gutter", scrollbar)
                    assert all(value == 0 for value in before["scroll"].values()), before["scroll"]
                    assert max(before["overflow"].values()) <= 1, before["overflow"]
                    assert len(before["selected"]) == 1
                    assert before["selected"][0]["text"] == TABS[area][lang == "ko"]
                    assert len(before["tabs"]) == 5
                    selected = before["selected"][0]["rect"]
                    assert selected["left"] >= before["nav"]["left"] - 1
                    assert selected["right"] <= before["nav"]["right"] + 1
                    content = before["first"]
                    assert content and content["text"] and not content["closed"], content
                    assert not content["suppressed"] and not content["clipped"], content
                    bounds = content["rect"]
                    assert bounds["width"] > 0 and bounds["height"] > 0, content
                    assert bounds["top"] >= 0 and bounds["bottom"] <= height + 1, content
                    if area in first_paragraph[lang]:
                        assert content["text"] == first_paragraph[lang][area], content
                        assert content["font"] >= 16, content
                        assert bounds["left"] >= 0 and bounds["right"] <= width + 1, content
                        heading = before["heading"]
                        assert heading and heading["text"] and not heading["closed"]
                        assert not heading["suppressed"] and not heading["clipped"], heading
                        assert heading["rect"]["top"] >= 0 and heading["rect"]["bottom"] <= height + 1, heading
                        assert before["authored"], "No authored content inspected"
                        assert all(not node["suppressed"] for node in before["authored"]), before["authored"]
                    if area in {"requirements", "code"}:
                        assert before["coverage"], "Coverage disclosure inventory is empty"
                    # Measure intersecting native summaries, classify partial targets honestly.
                    targets = before["tabs"] + [target for target in before["coverage"] if target["visibility"] != "outside"]
                    assert targets
                    for target in targets:
                        assert not target["closed"] and not target["suppressed"], target
                        assert target["rect"]["width"] >= 44 and target["rect"]["height"] >= 44, target
                        if target["visibility"] == "complete":
                            assert target["hit"], target
                    active = next(target for target in before["tabs"] if target["text"] == TABS[area][lang == "ko"])
                    assert active["visibility"] == "complete" and active["hit"], active
                    # Screenshot capture must not change the measured initial state or layout.
                    for key in ("viewport", "scroll", "overflow", "scrollbar", "host", "nav", "selected", "first", "heading", "tabs", "coverage", "authored"):
                        assert before[key] == after[key], (key, before[key], after[key])
                except AssertionError as error:
                    failures.append(f"{stem}: {error}; evidence={receipt}")
        assert _runs(directory) == 0
    assert not failures, "\n".join(failures)
