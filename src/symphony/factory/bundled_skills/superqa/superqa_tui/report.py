"""Report generation: Markdown + self-contained HTML, in the scenario's language.

Written for non-developers: plain sentences, step-by-step table, side effects
explained in words, screenshots inline. Secret values are masked.
"""
from __future__ import annotations

import html
import time
from pathlib import Path

from .engine import RunResult
from .i18n import t
from .store import Store

_EFFECT_KEYS = {
    "console_error": "effect_console_error",
    "console_warning": "effect_console_warning",
    "page_error": "effect_page_error",
    "request_failed": "effect_request_failed",
    "http_error": "effect_http_error",
    "dialog": "effect_dialog",
    "popup": "effect_popup",
    "download": "effect_download",
    "navigation": "effect_navigation",
    "visual_change": "effect_visual_change",
}


def _mask_all(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 3:
            text = text.replace(s, "*" * 6)
    return text


def _fmt_duration(result: RunResult) -> str:
    sec = max(0.0, (result.finished_at or time.time()) - result.started_at)
    return f"{sec:.1f}s"


def write_reports(result: RunResult, store: Store | None = None,
                  diff_lines: list[str] | None = None) -> tuple[Path, Path]:
    """Write report.md and report.html into the run dir; return their paths."""
    assert result.run_dir is not None
    lang = result.scenario.language or "ko"
    secrets = store.secret_values() if store else []
    md_path = result.run_dir / "report.md"
    html_path = result.run_dir / "report.html"
    md_path.write_text(_render_md(result, lang, secrets, diff_lines), encoding="utf-8")
    html_path.write_text(_render_html(result, lang, secrets, diff_lines), encoding="utf-8")
    return md_path, html_path


def _status_word(status: str, lang: str) -> str:
    return t("status_pass", lang) if status == "pass" else t("status_fail", lang)


def _render_md(r: RunResult, lang: str, secrets: list[str],
               diff_lines: list[str] | None = None) -> str:
    sc = r.scenario
    lines = [
        f"# {t('report_title', lang)}: {sc.name}",
        "",
        f"- {t('scenario_name', lang)}: {sc.name}",
        f"- {t('site', lang)}: {sc.site} ({sc.base_url})",
        f"- {t('started', lang)}: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r.started_at))}",
        f"- {t('duration', lang)}: {_fmt_duration(r)}",
        f"- {t('result', lang)}: **{_status_word(r.status, lang)}** - "
        + t("summary_line", lang, total=len(r.step_results), passed=r.passed, failed=r.failed),
        f"- {t('effects_line', lang, count=len(r.visible_effects))}",
        "",
    ]
    if r.run_dir and (r.run_dir / "trace.zip").exists():
        lines.insert(-1, f"- {t('trace_line', lang)}")
    if diff_lines:
        lines += [f"## {t('diff_title', lang)}", ""]
        lines += [f"- {ln}" if not ln.startswith("  ") else ln for ln in diff_lines]
        lines.append("")
    lines += [
        f"## {t('steps_summary', lang)}",
        "",
        f"| # | {t('step', lang)} | {t('result', lang)} | {t('screenshot', lang)} |",
        "|---|---|---|---|",
    ]
    for sr in r.step_results:
        desc = sr.step.description or f"{sr.step.action} {sr.step.url or sr.step.selector or ''}"
        desc = _mask_all(str(desc), secrets)
        status = _status_word("pass" if sr.status == "pass" else "fail", lang)
        if sr.status == "skipped":
            status = "-"
        err = f" ({_mask_all(sr.error, secrets)})" if sr.error else ""
        shot = f"[{sr.screenshot}]({sr.screenshot})" if sr.screenshot else "-"
        lines.append(f"| {sr.index + 1} | {desc} | {status}{err} | {shot} |")
    def effect_rows(effects) -> list[str]:
        rows = [f"| {t('step', lang)} | 종류 | 심각도 | {t('effect_count', lang)} | 내용 |"
                if lang == "ko"
                else f"| Step | Type | Severity | {t('effect_count', lang)} | Message |",
                "|---|---|---|---|---|"]
        for e in effects:
            kind = t(_EFFECT_KEYS.get(e.type, e.type), lang)
            sev = t(f"severity_{e.severity}", lang)
            step_no = "-" if e.step_index is None else str(e.step_index + 1)
            rows.append(f"| {step_no} | {kind} | {sev} | {e.count} | "
                        f"{_mask_all(e.message, secrets)} |")
        return rows

    lines += ["", f"## {t('side_effects', lang)}", ""]
    visible = r.visible_effects
    ignored = [e for e in r.effects if e.ignored]
    if not visible:
        lines.append(t("no_side_effects", lang))
    else:
        lines += effect_rows(visible)
    if ignored:
        lines += ["", f"### {t('ignored_effects', lang)}", ""]
        lines += effect_rows(ignored)
    lines.append("")
    return "\n".join(lines)


_CSS = """
body{font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;margin:0;
  background:#f8fafc;color:#0f172a;line-height:1.6}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px}
h1{font-size:24px;margin:0 0 4px}
.meta{color:#475569;font-size:14px;margin-bottom:20px}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-weight:700;font-size:14px}
.pass{background:#dcfce7;color:#166534}.fail{background:#fee2e2;color:#991b1b}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;
  box-shadow:0 1px 3px rgba(0,0,0,.08);margin:12px 0 28px;font-size:14px}
th{background:#f1f5f9;text-align:left;padding:10px 12px;font-size:13px;color:#475569}
td{padding:10px 12px;border-top:1px solid #e2e8f0;vertical-align:top}
.err{color:#b91c1c;font-size:12px;display:block;margin-top:2px;word-break:break-all}
img{max-width:320px;border-radius:6px;border:1px solid #e2e8f0;cursor:pointer}
img:hover{outline:2px solid #6366f1}
h2{font-size:18px;margin:28px 0 6px}
.sev-error{color:#b91c1c;font-weight:600}.sev-warning{color:#b45309}.sev-info{color:#475569}
.empty{color:#16a34a;background:#f0fdf4;padding:12px 16px;border-radius:10px;font-size:14px}
.msg{word-break:break-all;font-size:13px}
.muted{color:#64748b;font-size:15px;margin-top:20px}
.diff{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 18px 14px 34px;
  font-size:14px;margin:8px 0 4px}
.diff li{margin:2px 0}
"""


def _render_html(r: RunResult, lang: str, secrets: list[str],
                 diff_lines: list[str] | None = None) -> str:
    sc = r.scenario
    esc = lambda s: html.escape(_mask_all(str(s), secrets))
    badge = "pass" if r.status == "pass" else "fail"
    rows = []
    for sr in r.step_results:
        desc = sr.step.description or f"{sr.step.action} {sr.step.url or sr.step.selector or ''}"
        status = _status_word("pass" if sr.status == "pass" else "fail", lang)
        if sr.status == "skipped":
            status = "-"
        err = f"<span class='err'>{esc(sr.error)}</span>" if sr.error else ""
        shot = (f"<a href='{esc(sr.screenshot)}' target='_blank'>"
                f"<img src='{esc(sr.screenshot)}' loading='lazy'></a>") if sr.screenshot else "-"
        cls = "pass" if sr.status == "pass" else ("" if sr.status == "skipped" else "fail")
        rows.append(f"<tr><td>{sr.index + 1}</td><td>{esc(desc)}{err}</td>"
                    f"<td><span class='badge {cls}'>{esc(status)}</span></td><td>{shot}</td></tr>")
    def effect_table(effects) -> str:
        eff_rows = []
        for e in effects:
            kind = t(_EFFECT_KEYS.get(e.type, e.type), lang)
            sev = t(f"severity_{e.severity}", lang)
            step_no = "-" if e.step_index is None else str(e.step_index + 1)
            eff_rows.append(f"<tr><td>{step_no}</td><td>{esc(kind)}</td>"
                            f"<td class='sev-{e.severity}'>{esc(sev)}</td>"
                            f"<td>{e.count}</td>"
                            f"<td class='msg'>{esc(e.message)}</td></tr>")
        head = (f"<tr><th>단계</th><th>종류</th><th>심각도</th>"
                f"<th>{esc(t('effect_count', lang))}</th><th>내용</th></tr>" if lang == "ko"
                else f"<tr><th>Step</th><th>Type</th><th>Severity</th>"
                     f"<th>{esc(t('effect_count', lang))}</th><th>Message</th></tr>")
        return f"<table>{head}{''.join(eff_rows)}</table>"

    visible = r.visible_effects
    ignored = [e for e in r.effects if e.ignored]
    effects_html = (effect_table(visible) if visible
                    else f"<p class='empty'>{esc(t('no_side_effects', lang))}</p>")
    if ignored:
        effects_html += (f"<h3 class='muted'>{esc(t('ignored_effects', lang))}</h3>"
                         + effect_table(ignored))
    diff_html = ""
    if diff_lines:
        items = "".join(f"<li>{esc(ln)}</li>" for ln in diff_lines)
        diff_html = (f"<h2>{esc(t('diff_title', lang))}</h2>"
                     f"<ul class='diff'>{items}</ul>")
    if r.run_dir and (r.run_dir / "trace.zip").exists():
        diff_html = (f"<p class='meta'>{esc(t('trace_line', lang))}</p>") + diff_html
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.started_at))
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(t('report_title', lang))}: {esc(sc.name)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{esc(t('report_title', lang))}: {esc(sc.name)}
 <span class="badge {badge}">{esc(_status_word(r.status, lang))}</span></h1>
<p class="meta">{esc(t('site', lang))}: {esc(sc.site)} · {esc(sc.base_url)}<br>
{esc(t('started', lang))}: {started} · {esc(t('duration', lang))}: {_fmt_duration(r)}<br>
{esc(t('summary_line', lang, total=len(r.step_results), passed=r.passed, failed=r.failed))} ·
{esc(t('effects_line', lang, count=len(r.visible_effects)))}</p>
{diff_html}
<h2>{esc(t('steps_summary', lang))}</h2>
<table><tr><th>#</th><th>{esc(t('step', lang))}</th><th>{esc(t('result', lang))}</th>
<th>{esc(t('screenshot', lang))}</th></tr>{''.join(rows)}</table>
<h2>{esc(t('side_effects', lang))}</h2>
{effects_html}
</div></body></html>"""


def write_index(store: Store, lang: str | None = None) -> Path:
    """Refresh ~/.superqa/reports/index.html - run history for non-developers."""
    from .i18n import default_lang
    from .scenario import superqa_home
    lang = lang or default_lang()
    rows = []
    for r in store.recent_runs(100):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started_at"]))
        ok = r["status"] == "pass"
        word = t("status_pass", lang) if ok else (
            t("status_fail", lang) if r["status"] == "fail" else r["status"])
        link = (f"<a href='file://{html.escape(str(r['report_path']))}'>"
                f"{html.escape(str(r['scenario']))}</a>"
                if r.get("report_path") else html.escape(str(r["scenario"])))
        rows.append(
            f"<tr><td>{when}</td><td>{link}</td><td>{html.escape(str(r['site']))}</td>"
            f"<td><span class='badge {'pass' if ok else 'fail'}'>{word}</span></td>"
            f"<td>{r['passed']}/{r['passed'] + r['failed']}</td><td>{r['effects']}</td></tr>")
    head = ("<tr><th>시각</th><th>시나리오</th><th>사이트</th><th>결과</th>"
            "<th>단계</th><th>부작용</th></tr>" if lang == "ko" else
            "<tr><th>Time</th><th>Scenario</th><th>Site</th><th>Result</th>"
            "<th>Steps</th><th>Effects</th></tr>")
    title = t("runs_index_title", lang)
    out = superqa_home() / "reports" / "index.html"
    out.write_text(
        f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body><div class="wrap"><h1>{html.escape(title)}</h1>
<table>{head}{''.join(rows)}</table></div></body></html>""",
        encoding="utf-8")
    return out
