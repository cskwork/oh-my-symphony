"""Browser engine: scenario replay, click recording, auto smoke QA.

Playwright async API (runs inside Textual's event loop or a plain asyncio.run).
Everything observable is captured as a SideEffect: console errors, JS exceptions,
failed/4xx-5xx requests, dialogs, new tabs/popups, downloads.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from playwright.async_api import (
    BrowserContext,
    Dialog,
    Download,
    Page,
    async_playwright,
)

from .scenario import (
    Policy,
    Scenario,
    Step,
    safe_name,
    site_ignore_patterns,
    superqa_home,
)
from .store import Store
from .visual import run_visual_checks

EventCb = Callable[[dict], None]

# resource types that produce noisy, non-actionable request failures
_IGNORED_FAILURES = ("net::ERR_ABORTED",)


@dataclass
class SideEffect:
    type: str          # console_error|console_warning|page_error|request_failed|http_error|dialog|popup|download|navigation
    severity: str      # error|warning|info
    message: str
    step_index: int | None = None
    at: float = field(default_factory=time.time)
    count: int = 1     # identical effects are deduped and counted
    ignored: bool = False  # matched a noise pattern (site/scenario ignore rules)

    def to_dict(self) -> dict:
        return {
            "type": self.type, "severity": self.severity, "message": self.message,
            "step_index": self.step_index, "at": self.at,
            "count": self.count, "ignored": self.ignored,
        }

    def digest(self) -> str:
        """Stable key for run-to-run comparison: digits collapsed, prefix only."""
        norm = re.sub(r"\d+", "#", self.message[:100])
        return f"{self.type}:{norm}"


@dataclass
class StepResult:
    index: int
    step: Step
    status: str            # pass|fail|skipped
    error: str = ""
    screenshot: str = ""   # relative path inside run dir
    duration_ms: int = 0


@dataclass
class RunResult:
    scenario: Scenario
    started_at: float
    finished_at: float = 0.0
    step_results: list[StepResult] = field(default_factory=list)
    effects: list[SideEffect] = field(default_factory=list)
    run_dir: Path | None = None
    status: str = "running"    # pass|fail|error

    @property
    def passed(self) -> int:
        return sum(1 for r in self.step_results if r.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.step_results if r.status == "fail")

    @property
    def visible_effects(self) -> list[SideEffect]:
        return [e for e in self.effects if not e.ignored]

    def summary_dict(self) -> dict:
        """Compact run fingerprint persisted per run and diffed against the next one."""
        return {
            "status": self.status,
            "passed": self.passed,
            "failed": self.failed,
            "failed_steps": [
                (r.step.description or r.step.action)
                for r in self.step_results if r.status == "fail"],
            "effect_digests": sorted({e.digest() for e in self.visible_effects}),
        }


def _mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 3:
            text = text.replace(s, "*" * 6)
    return text


class EffectCollector:
    """Attach listeners to a context; every page (incl. popups) is covered."""

    def __init__(self, policy: Policy, on_event: EventCb | None = None,
                 ignore_patterns: list[str] | None = None):
        self.policy = policy
        self.effects: list[SideEffect] = []
        self.current_step: int | None = None
        self.on_event = on_event
        self.new_pages: list[Page] = []
        self._seen_http: set[str] = set()
        self._by_key: dict[tuple[str, str], SideEffect] = {}
        self.ignore_patterns = list(ignore_patterns or [])

    def _add(self, type_: str, severity: str, message: str) -> None:
        message = message[:500]
        key = (type_, message)
        if key in self._by_key:               # dedupe: same effect again -> count up
            self._by_key[key].count += 1
            return
        ignored = any(pat and pat in message for pat in self.ignore_patterns)
        eff = SideEffect(type_, severity, message, self.current_step, ignored=ignored)
        self._by_key[key] = eff
        self.effects.append(eff)
        if self.on_event and not ignored:
            self.on_event({"kind": "effect", **eff.to_dict()})

    def attach_context(self, context: BrowserContext) -> None:
        context.on("page", self._on_page)
        for page in context.pages:
            self._attach_page(page)

    def _on_page(self, page: Page) -> None:
        self.new_pages.append(page)
        self._add("popup", "info", page.url or "(새 탭)")
        self._attach_page(page)

    def _attach_page(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self._add("page_error", "error", str(e)))
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)
        page.on("download", lambda d: self._add("download", "info", _dl_name(d)))

    def _on_console(self, msg) -> None:
        if msg.type == "error":
            self._add("console_error", "error", msg.text)
        elif msg.type == "warning":
            self._add("console_warning", "warning", msg.text)

    def _on_request_failed(self, request) -> None:
        failure = request.failure or ""
        if any(x in failure for x in _IGNORED_FAILURES):
            return
        self._add("request_failed", "error", f"{request.method} {request.url} - {failure}")

    def _on_response(self, response) -> None:
        if response.status < 400:
            return
        key = f"{response.status} {response.url}"
        if key in self._seen_http:
            return
        self._seen_http.add(key)
        sev = "error" if response.status >= 500 else "warning"
        self._add("http_error", sev, key)

    def _on_dialog(self, dialog: Dialog) -> None:
        self._add("dialog", "info", f"{dialog.type}: {dialog.message}")
        action = self.policy.dialogs
        coro = dialog.dismiss() if action == "dismiss" else dialog.accept()
        asyncio.ensure_future(coro)


def _dl_name(d: Download) -> str:
    try:
        return d.suggested_filename
    except Exception:
        return "download"


async def _resolve(page: Page, selector: Any, timeout_ms: int):
    """Selector fallback chain. dict keys: testid, css, role+name, text."""
    if isinstance(selector, str):
        loc = page.locator(selector) if not selector.startswith("text=") \
            else page.get_by_text(selector[5:], exact=False)
        return loc.first
    if isinstance(selector, dict):
        candidates = []
        if selector.get("testid"):
            candidates.append(page.get_by_test_id(selector["testid"]))
        if selector.get("css"):
            candidates.append(page.locator(selector["css"]))
        if selector.get("role") and selector.get("name"):
            candidates.append(page.get_by_role(selector["role"], name=selector["name"]))
        if selector.get("text"):
            candidates.append(page.get_by_text(str(selector["text"]), exact=False))
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while time.monotonic() < deadline:
            for cand in candidates:
                loc = cand.first
                try:
                    if await loc.count() > 0:
                        return loc
                except Exception as e:
                    last = e
            await asyncio.sleep(0.25)
        raise TimeoutError(f"element not found: {selector} ({last or 'no match'})")
    raise ValueError(f"bad selector: {selector!r}")


class Engine:
    def __init__(self, store: Store | None = None, headed: bool = True,
                 on_event: EventCb | None = None, slow_mo: int = 0):
        self.store = store or Store()
        self.headed = headed
        self.on_event = on_event
        self.slow_mo = slow_mo

    def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event({"kind": kind, **data})

    # ---- run ----------------------------------------------------------------
    async def run_scenario(self, sc: Scenario) -> RunResult:
        result = RunResult(scenario=sc, started_at=time.time())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = superqa_home() / "reports" / f"{stamp}-{_safe(sc.name)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result.run_dir = run_dir

        ignore = sc.policy.ignore_effects + site_ignore_patterns(sc.site)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed, slow_mo=self.slow_mo)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.tracing.start(screenshots=True, snapshots=True)
            collector = EffectCollector(sc.policy, self.on_event, ignore)
            collector.attach_context(context)
            page = await context.new_page()
            try:
                await self._run_steps(sc, page, context, collector, result, run_dir)
            finally:
                result.effects = collector.effects
                self._append_visual_effects(sc, result, run_dir)
                result.finished_at = time.time()
                result.status = self._final_status(sc.policy, result)
                await self._save_trace(context, result, run_dir)
                await browser.close()
        return result

    def _append_visual_effects(self, sc: Scenario, result: RunResult, run_dir: Path) -> None:
        try:
            findings = run_visual_checks(sc, result.step_results, run_dir)
        except Exception:
            return  # visual layer must never break a run
        for f in findings:
            eff = SideEffect("visual_change", "warning", f["message"], f["step_index"])
            result.effects.append(eff)
            self._emit("effect", **eff.to_dict())

    async def _save_trace(self, context: BrowserContext, result: RunResult,
                          run_dir: Path) -> None:
        """Keep the Playwright trace only when something failed - debugging evidence."""
        try:
            if result.failed:
                await context.tracing.stop(path=str(run_dir / "trace.zip"))
            else:
                await context.tracing.stop()
        except Exception:
            pass

    async def _run_steps(self, sc: Scenario, page: Page, context: BrowserContext,
                         collector: EffectCollector, result: RunResult, run_dir: Path) -> None:
        active = page
        for i, step in enumerate(sc.steps):
            collector.current_step = i
            self._emit("step_start", index=i, action=step.action,
                       description=step.description)
            t0 = time.monotonic()
            sr = StepResult(index=i, step=step, status="pass")
            try:
                for attempt in range(step.retry + 1):
                    try:
                        active = await self._exec_step(sc, step, active, context)
                        break
                    except Exception:
                        if attempt >= step.retry:
                            raise
                        self._emit("step_retry", index=i, attempt=attempt + 1)
                        await asyncio.sleep(1.0)
                await self._shot(active, run_dir, i, sr)
            except Exception as e:
                sr.status = "skipped" if step.optional else "fail"
                sr.error = _mask(str(e), self.store.secret_values())[:400]
                await self._shot(active, run_dir, i, sr)
            sr.duration_ms = int((time.monotonic() - t0) * 1000)
            result.step_results.append(sr)
            self._emit("step_end", index=i, status=sr.status, error=sr.error)
            if sr.status == "fail":
                break

    async def _exec_step(self, sc: Scenario, step: Step, page: Page,
                         context: BrowserContext) -> Page:
        """Run one step; returns the page follow-up steps should target."""
        store, site, tmo = self.store, sc.site, step.timeout_ms
        sub = lambda s: store.substitute(site, s) if s else s

        if step.action == "goto":
            await page.goto(sub(step.url or sc.base_url), timeout=max(tmo, 30000),
                            wait_until="domcontentloaded")
            return page
        if step.action == "wait":
            await asyncio.sleep(float(step.value or 1))
            return page
        if step.action == "screenshot":
            return page
        if step.action == "expect_url":
            await page.wait_for_url(re.compile(re.escape(sub(step.url or ""))), timeout=tmo)
            return page
        if step.action == "switch_tab":
            return await self._switch_tab(step, context, tmo)
        if step.action == "close_tab":
            await page.close()
            return context.pages[-1] if context.pages else page
        if step.action == "scroll":
            await page.mouse.wheel(0, int(step.value or 600))
            return page
        if step.action == "login":
            return await self._login_macro(sc, page)

        loc = await _resolve(page, step.selector, tmo)
        if step.action in ("click", "dblclick") and (step.expect_popup or sc.policy.popups == "follow"):
            return await self._click_maybe_popup(step, page, context, loc, tmo)
        if step.action == "click":
            await loc.click(timeout=tmo)
        elif step.action == "dblclick":
            await loc.dblclick(timeout=tmo)
        elif step.action == "fill":
            await loc.fill(sub(step.value or ""), timeout=tmo)
        elif step.action == "press":
            await loc.press(step.value or "Enter", timeout=tmo)
        elif step.action == "select":
            await loc.select_option(sub(step.value or ""), timeout=tmo)
        elif step.action == "check":
            await loc.check(timeout=tmo)
        elif step.action == "uncheck":
            await loc.uncheck(timeout=tmo)
        elif step.action == "hover":
            await loc.hover(timeout=tmo)
        elif step.action == "expect_visible":
            await loc.wait_for(state="visible", timeout=tmo)
        elif step.action == "expect_text":
            await loc.wait_for(state="visible", timeout=tmo)
            text = (await loc.inner_text()) or ""
            want = sub(step.value or "")
            if want not in text:
                raise AssertionError(f"text mismatch: want {want!r} in {text[:120]!r}")
        else:
            raise ValueError(f"unhandled action: {step.action}")
        return page

    async def _click_maybe_popup(self, step: Step, page: Page, context: BrowserContext,
                                 loc, tmo: int) -> Page:
        """Click that may open a new tab. Follow it when one appears."""
        before = list(context.pages)
        if step.action == "dblclick":
            await loc.dblclick(timeout=tmo)
        else:
            await loc.click(timeout=tmo)
        deadline = time.monotonic() + (2.5 if not step.expect_popup else tmo / 1000)
        while time.monotonic() < deadline:
            new = [p for p in context.pages if p not in before and not p.is_closed()]
            if new:
                target = new[-1]
                await target.wait_for_load_state("domcontentloaded", timeout=tmo)
                await target.bring_to_front()
                return target
            await asyncio.sleep(0.2)
        if step.expect_popup:
            raise TimeoutError("expected a new tab/popup but none opened")
        return page if not page.is_closed() else context.pages[-1]

    async def _switch_tab(self, step: Step, context: BrowserContext, tmo: int) -> Page:
        which = (step.value or "latest").strip()
        deadline = time.monotonic() + tmo / 1000
        while time.monotonic() < deadline:
            pages = [p for p in context.pages if not p.is_closed()]
            if which == "latest" and pages:
                await pages[-1].bring_to_front()
                return pages[-1]
            if which.isdigit() and int(which) < len(pages):
                await pages[int(which)].bring_to_front()
                return pages[int(which)]
            await asyncio.sleep(0.2)
        raise TimeoutError(f"tab not found: {which}")

    async def _login_macro(self, sc: Scenario, page: Page) -> Page:
        """Best-effort login using stored vars: username/password + common fields."""
        user = self.store.get_var(sc.site, "username") or self.store.get_var(sc.site, "id")
        pw = self.store.get_var(sc.site, "password") or self.store.get_var(sc.site, "pw")
        if not user or not pw:
            raise ValueError("login vars missing: set 'username'/'password' for site "
                             f"'{sc.site}' (superqa vars set {sc.site} username ...)")
        user_loc = page.locator(
            "input[type='text'], input[type='email'], input[name*='id' i], "
            "input[name*='user' i], input[placeholder*='아이디']").first
        pw_loc = page.locator("input[type='password']").first
        await user_loc.fill(user, timeout=10000)
        await pw_loc.fill(pw, timeout=10000)
        btn = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button:has-text('로그인'), button:has-text('Login'), a:has-text('로그인')").first
        await btn.click(timeout=10000)
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        return page

    async def _shot(self, page: Page, run_dir: Path, index: int, sr: StepResult) -> None:
        try:
            name = f"step-{index:02d}.png"
            await page.screenshot(path=str(run_dir / name), timeout=5000)
            sr.screenshot = name
        except Exception:
            pass  # screenshot is evidence, not a gate

    def _final_status(self, policy: Policy, result: RunResult) -> str:
        if result.failed:
            return "fail"
        if policy.fail_on_console_error and any(
                e.type in ("console_error", "page_error") for e in result.effects):
            return "fail"
        if policy.fail_on_http_error and any(
                e.type in ("http_error", "request_failed") for e in result.effects):
            return "fail"
        return "pass"

    # ---- auto smoke -----------------------------------------------------------
    async def auto_smoke(self, url: str, site: str = "default", max_links: int = 8,
                         language: str = "ko") -> RunResult:
        """One-button QA: open page, collect effects, visit same-origin nav links."""
        sc = Scenario(name=f"자동QA-{urlparse(url).netloc or 'local'}", site=site,
                      base_url=url, language=language,
                      steps=[Step(action="goto", url=url, description=f"{url} 접속")])
        result = RunResult(scenario=sc, started_at=time.time())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = superqa_home() / "reports" / f"{stamp}-{_safe(sc.name)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result.run_dir = run_dir

        ignore = site_ignore_patterns(site)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed, slow_mo=self.slow_mo)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            collector = EffectCollector(sc.policy, self.on_event, ignore)
            collector.attach_context(context)
            page = await context.new_page()
            try:
                await self._smoke_pass(sc, page, url, max_links, collector, result, run_dir)
            finally:
                result.effects = collector.effects
                result.finished_at = time.time()
                result.status = "pass" if not result.failed else "fail"
                await browser.close()
        return result

    async def _smoke_pass(self, sc: Scenario, page: Page, url: str, max_links: int,
                          collector: EffectCollector, result: RunResult, run_dir: Path) -> None:
        collector.current_step = 0
        self._emit("step_start", index=0, action="goto", description=url)
        sr = StepResult(index=0, step=sc.steps[0], status="pass")
        try:
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
            await self._shot(page, run_dir, 0, sr)
        except Exception as e:
            sr.status, sr.error = "fail", str(e)[:400]
        result.step_results.append(sr)
        self._emit("step_end", index=0, status=sr.status, error=sr.error)
        if sr.status == "fail":
            return
        origin = urlparse(page.url).netloc
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href).filter(h => h.startsWith('http'))")
        seen, targets = set(), []
        for h in hrefs:
            if urlparse(h).netloc == origin and h not in seen and "#" not in h:
                seen.add(h)
                targets.append(h)
            if len(targets) >= max_links:
                break
        for j, link in enumerate(targets, start=1):
            collector.current_step = j
            step = Step(action="goto", url=link, description=f"링크 점검: {link}")
            sc.steps.append(step)
            self._emit("step_start", index=j, action="goto", description=link)
            sr = StepResult(index=j, step=step, status="pass")
            try:
                await page.goto(link, timeout=30000, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                await self._shot(page, run_dir, j, sr)
            except Exception as e:
                sr.status, sr.error = "fail", str(e)[:400]
            result.step_results.append(sr)
            self._emit("step_end", index=j, status=sr.status, error=sr.error)

    # ---- record -----------------------------------------------------------------
    async def record(self, url: str, site: str = "default", name: str = "",
                     language: str = "ko", stop_event: asyncio.Event | None = None,
                     driver: Callable[[Page], Awaitable[None]] | None = None) -> Scenario:
        """Open a browser with the SuperQA overlay; user clicks build steps.

        Finishes when the user presses Save on the overlay, the browser closes,
        or stop_event is set. `driver` is a test hook that simulates the user.
        """
        overlay_js = (Path(__file__).parent / "recorder_overlay.js").read_text(encoding="utf-8")
        steps: list[Step] = [Step(action="goto", url=url, description=f"{url} 접속")]
        done = asyncio.Event()
        run_cb: dict[str, Any] = {}

        def on_record(payload: dict) -> None:
            kind = payload.get("kind")
            if kind == "step":
                step = _step_from_record(payload)
                if step:
                    _merge_step(steps, step)
                    self._emit("recorded", step=step.to_dict(), count=len(steps))
            elif kind == "save":
                done.set()
            elif kind == "run":
                run_cb["requested"] = True
                done.set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.add_init_script(overlay_js)
            await context.expose_binding(
                "__superqa_emit", lambda _source, payload: on_record(payload))
            context.on("page", lambda p: p.on("close", lambda _: _maybe_done(context, done)))
            page = await context.new_page()
            page.on("close", lambda _: _maybe_done(context, done))
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            driver_task = asyncio.ensure_future(driver(page)) if driver else None
            waiters = [asyncio.create_task(done.wait())]
            if stop_event:
                waiters.append(asyncio.create_task(stop_event.wait()))
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for w in waiters:
                w.cancel()
            if driver_task and not driver_task.done():
                driver_task.cancel()
            await browser.close()

        self._map_recorded_vars(site, steps)
        sc = Scenario(name=name or f"기록-{time.strftime('%m%d-%H%M')}", site=site,
                      base_url=url, language=language, steps=steps)
        sc.save()
        self._emit("record_done", path=str(sc.path), steps=len(steps),
                   run_requested=bool(run_cb.get("requested")))
        return sc

    def _map_recorded_vars(self, site: str, steps: list[Step]) -> None:
        """Typed values that equal a stored var become {{key}} references.

        Keeps recorded scenarios portable and keeps personal values (ids, emails)
        out of YAML files. Passwords are already masked at capture time.
        """
        value_to_key = {
            r["value"]: r["key"]
            for r in self.store.list_vars(site)
            if r["value"] and len(r["value"]) >= 3
        }
        for step in steps:
            if step.action == "fill" and step.value in value_to_key:
                step.value = "{{" + value_to_key[step.value] + "}}"


def _maybe_done(context: BrowserContext, done: asyncio.Event) -> None:
    if not [p for p in context.pages if not p.is_closed()]:
        done.set()


_safe = safe_name  # shared with visual baselines so paths always agree


def _step_from_record(payload: dict) -> Step | None:
    action = payload.get("action")
    sel = payload.get("selector")
    if action not in ("click", "fill", "press", "expect_visible", "goto", "select"):
        return None
    return Step(
        action=action,
        selector=sel,
        value=payload.get("value"),
        url=payload.get("url"),
        description=str(payload.get("description", ""))[:120],
    )


def _merge_step(steps: list[Step], step: Step) -> None:
    """Collapse consecutive fills on the same element; drop nav duplicates."""
    if steps:
        last = steps[-1]
        if (step.action == "fill" and last.action == "fill"
                and step.selector == last.selector):
            steps[-1] = step
            return
        if step.action == "goto" and last.action == "goto" and step.url == last.url:
            return
    steps.append(step)
