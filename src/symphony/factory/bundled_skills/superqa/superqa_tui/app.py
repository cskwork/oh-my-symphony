"""SuperQA TUI - a Textual app non-developers can drive with single keys.

Left: scenario list. Right: live run log + recent results.
Keys (Korean-first labels in the footer):
  r 실행 / a 전체 실행 / n 새 기록 / u 자동 QA / s 스케줄 / v 변수 / o 리포트 / q 종료
Recording opens a real browser with the SuperQA overlay; clicks become steps.
"""
from __future__ import annotations

import asyncio
import time
import webbrowser

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

import json

from .diff import compute_diff, format_diff_lines
from .engine import Engine
from .i18n import t
from .report import write_index, write_reports
from .scenario import Scenario, broken_scenarios, list_scenarios
from .scheduler import add_schedule, load_schedules, run_loop
from .store import Store


class AskScreen(ModalScreen[dict | None]):
    """Generic small form: one or more labelled inputs, 확인/취소."""

    def __init__(self, title: str, fields: list[tuple[str, str, str]]):
        # fields: (key, label, default)
        super().__init__()
        self._title = title
        self._fields = fields

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-box"):
            yield Label(self._title, id="ask-title")
            for key, label, default in self._fields:
                yield Label(label, classes="ask-label")
                yield Input(value=default, id=f"ask-{key}")
            with Horizontal(id="ask-buttons"):
                yield Button("확인", variant="success", id="ask-ok")
                yield Button("취소", id="ask-cancel")

    @on(Button.Pressed, "#ask-ok")
    def _ok(self) -> None:
        out = {}
        for key, _, _ in self._fields:
            out[key] = self.query_one(f"#ask-{key}", Input).value.strip()
        self.dismiss(out)

    @on(Button.Pressed, "#ask-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self._ok()


class SuperQAApp(App):
    TITLE = "SuperQA"
    CSS_PATH = "superqa.tcss"
    BINDINGS = [
        Binding("r", "run_selected", "실행"),
        Binding("a", "run_all", "전체 실행"),
        Binding("n", "record", "새 기록"),
        Binding("u", "auto", "자동 QA"),
        Binding("s", "schedule", "스케줄"),
        Binding("v", "vars", "계정/변수"),
        Binding("o", "open_report", "리포트"),
        Binding("q", "quit", "종료"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.store = Store()
        self.scenarios: list[Scenario] = []
        self._busy = False
        self._sched_stop: asyncio.Event | None = None

    # ---- layout ---------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label(t("scenarios"), classes="pane-title")
                yield DataTable(id="scenario-table", cursor_type="row")
                yield Static("", id="hint")
            with Vertical(id="right"):
                yield Label(t("run_log"), classes="pane-title")
                yield RichLog(id="log", wrap=True, markup=True, max_lines=500)
                yield Label(t("recent_runs"), classes="pane-title")
                yield DataTable(id="runs-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#scenario-table", DataTable)
        table.add_columns("사이트", "시나리오", "단계", "태그")
        runs = self.query_one("#runs-table", DataTable)
        runs.add_columns("시각", "시나리오", "결과", "성공/실패", "부작용")
        self.refresh_scenarios()
        self.refresh_runs()
        self._arm_schedules()
        self.log_line(f"[bold]{t('app_title')}[/bold]")
        if not self.scenarios:
            self.log_line(t("no_scenarios"))

    # ---- helpers ----------------------------------------------------------------
    def log_line(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def refresh_scenarios(self) -> None:
        self.scenarios = list_scenarios()
        table = self.query_one("#scenario-table", DataTable)
        table.clear()
        for sc in self.scenarios:
            table.add_row(sc.site, sc.name, str(len(sc.steps)), ",".join(sc.tags))
        for path, err in broken_scenarios():
            self.log_line(f"[yellow]경고: 읽을 수 없는 시나리오 {path.name} - {err}[/yellow]")
        sched = {e.scenario for e in load_schedules() if e.enabled}
        hint = self.query_one("#hint", Static)
        hint.update(f"스케줄 활성: {len(sched)}건" if sched else "")

    def refresh_runs(self) -> None:
        runs = self.query_one("#runs-table", DataTable)
        runs.clear()
        for r in self.store.recent_runs(10):
            when = time.strftime("%m-%d %H:%M", time.localtime(r["started_at"]))
            word = {"pass": t("status_pass"), "fail": t("status_fail")}.get(
                r["status"], t("status_running"))
            runs.add_row(when, r["scenario"], word,
                         f"{r['passed']}/{r['passed'] + r['failed']}", str(r["effects"]))

    def selected_scenario(self) -> Scenario | None:
        table = self.query_one("#scenario-table", DataTable)
        if not self.scenarios or table.cursor_row is None:
            return None
        idx = max(0, min(table.cursor_row, len(self.scenarios) - 1))
        return self.scenarios[idx]

    def _engine_event(self, ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "step_start":
            desc = ev.get("description") or ev.get("action")
            self.log_line(f"  [{ev['index'] + 1}] {desc} ...")
        elif kind == "step_end":
            ok = ev["status"] == "pass"
            color = "green" if ok else ("yellow" if ev["status"] == "skipped" else "red")
            word = t("status_pass") if ok else t("status_fail")
            err = f" - {ev.get('error')}" if ev.get("error") else ""
            self.log_line(f"      [{color}]{word}[/{color}]{err}")
        elif kind == "effect":
            sev = ev.get("severity")
            if sev in ("error", "warning"):
                color = "red" if sev == "error" else "yellow"
                self.log_line(f"      [{color}]부작용: {ev.get('type')} - "
                              f"{str(ev.get('message'))[:100]}[/{color}]")
        elif kind == "recorded":
            step = ev.get("step", {})
            self.log_line(f"  기록 {ev.get('count')}: {step.get('description', '')}")

    # ---- run ---------------------------------------------------------------------
    async def _run_scenario(self, sc: Scenario, headed: bool = True) -> None:
        run_id = self.store.start_run(sc.name, sc.site)
        self.log_line(f"[bold]{t('run')}: {sc.name}[/bold] ({sc.site})")
        engine = Engine(store=self.store, headed=headed, on_event=self._engine_event)
        result = await engine.run_scenario(sc)
        summary = result.summary_dict()
        prev = self.store.previous_run(sc.name, run_id)
        diff_lines = format_diff_lines(
            compute_diff(prev.get("summary") if prev else None, summary))
        _, html_p = write_reports(result, self.store, diff_lines)
        self.store.finish_run(run_id, result.status, result.passed, result.failed,
                              len(result.visible_effects), str(html_p),
                              json.dumps(summary, ensure_ascii=False))
        write_index(self.store)
        color = "green" if result.status == "pass" else "red"
        word = t("status_pass") if result.status == "pass" else t("status_fail")
        self.log_line(
            f"[{color} bold]{word}[/{color} bold] - "
            + t("summary_line", total=len(result.step_results),
                passed=result.passed, failed=result.failed)
            + " / " + t("effects_line", count=len(result.visible_effects)))
        self.log_line(f"[cyan]{t('diff_title')}[/cyan]")
        for ln in diff_lines:
            self.log_line(f"  {ln}")
        self.log_line(f"리포트: {html_p}")
        self.refresh_runs()

    @work(exclusive=True)
    async def action_run_selected(self) -> None:
        sc = self.selected_scenario()
        if not sc:
            self.log_line(t("no_scenarios"))
            return
        await self._run_scenario(sc)

    @work(exclusive=True)
    async def action_run_all(self) -> None:
        if not self.scenarios:
            self.log_line(t("no_scenarios"))
            return
        self.log_line(f"[bold]{t('run_all')}[/bold] ({len(self.scenarios)}건)")
        for sc in self.scenarios:
            await self._run_scenario(sc)

    # ---- record / auto ---------------------------------------------------------------
    @work(exclusive=True)
    async def action_record(self) -> None:
        answers = await self.push_screen_wait(AskScreen(
            t("record"), [
                ("url", t("enter_url"), "https://"),
                ("site", t("enter_site"), "default"),
                ("name", t("enter_name"), ""),
            ]))
        if not answers or not answers.get("url", "").startswith("http"):
            return
        self.log_line(t("record_hint"))
        engine = Engine(store=self.store, headed=True, on_event=self._engine_event)
        sc = await engine.record(answers["url"], site=answers.get("site") or "default",
                                 name=answers.get("name") or "")
        self.log_line(f"{t('saved')}: {sc.path} ({len(sc.steps)}단계)")
        self.refresh_scenarios()

    @work(exclusive=True)
    async def action_auto(self) -> None:
        answers = await self.push_screen_wait(AskScreen(
            t("auto"), [("url", t("enter_url"), "https://"),
                        ("site", t("enter_site"), "default")]))
        if not answers or not answers.get("url", "").startswith("http"):
            return
        site = answers.get("site") or "default"
        from urllib.parse import urlparse
        run_name = f"자동QA-{urlparse(answers['url']).netloc or 'local'}"
        run_id = self.store.start_run(run_name, site)
        self.log_line(f"[bold]{t('auto')}: {answers['url']}[/bold]")
        engine = Engine(store=self.store, headed=True, on_event=self._engine_event)
        result = await engine.auto_smoke(answers["url"], site=site)
        summary = result.summary_dict()
        prev = self.store.previous_run(run_name, run_id)
        diff_lines = format_diff_lines(
            compute_diff(prev.get("summary") if prev else None, summary))
        _, html_p = write_reports(result, self.store, diff_lines)
        self.store.finish_run(run_id, result.status, result.passed, result.failed,
                              len(result.visible_effects), str(html_p),
                              json.dumps(summary, ensure_ascii=False))
        write_index(self.store)
        self.log_line(f"리포트: {html_p}")
        self.refresh_runs()

    # ---- schedule / vars / report -----------------------------------------------------
    @work(exclusive=True)
    async def action_schedule(self) -> None:
        sc = self.selected_scenario()
        if not sc:
            self.log_line(t("no_scenarios"))
            return
        answers = await self.push_screen_wait(AskScreen(
            t("schedule"), [("minutes", t("enter_interval"), "30")]))
        if not answers:
            return
        try:
            minutes = max(1, int(answers.get("minutes", "30")))
        except ValueError:
            return
        add_schedule(sc.name, minutes)
        self.log_line(t("schedule_armed", name=sc.name, minutes=minutes))
        self.log_line(t("schedule_hint"))
        self.refresh_scenarios()

    @work(exclusive=True)
    async def action_vars(self) -> None:
        current = self.store.list_vars()
        if current:
            self.log_line("저장된 변수:")
            for r in current:
                val = "******" if r["secret"] else r["value"]
                self.log_line(f"  {r['site']} / {r['key']} = {val}")
        answers = await self.push_screen_wait(AskScreen(
            t("vars"), [("site", t("enter_site"), "default"),
                        ("key", "키 (예: username, password)", ""),
                        ("value", "값", "")]))
        if not answers or not answers.get("key"):
            return
        self.store.set_var(answers.get("site") or "default", answers["key"],
                           answers.get("value", ""))
        self.log_line(f"{t('saved')}: {answers.get('site')}/{answers['key']} "
                      "(비밀번호류는 리포트에서 자동 마스킹)")

    def action_open_report(self) -> None:
        runs = self.store.recent_runs(1)
        if not runs or not runs[0].get("report_path"):
            self.log_line("리포트가 없습니다.")
            return
        webbrowser.open(f"file://{runs[0]['report_path']}")

    # ---- background schedules while TUI is open ------------------------------------------
    def _arm_schedules(self) -> None:
        self._sched_stop = asyncio.Event()

        async def runner(name: str) -> None:
            from .scenario import find_scenario
            try:
                sc = find_scenario(name)
            except FileNotFoundError:
                return
            self.log_line(f"[cyan]스케줄 실행: {name}[/cyan]")
            await self._run_scenario(sc, headed=False)

        self._sched_task = asyncio.ensure_future(run_loop(runner, self._sched_stop))

    async def action_quit(self) -> None:
        if self._sched_stop:
            self._sched_stop.set()
        self.exit()


if __name__ == "__main__":
    SuperQAApp().run()
