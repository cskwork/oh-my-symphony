"""Local web admin - a clickable dashboard, synced with the TUI's data.

Serves a single-page app from the Python standard library (no Flask, no extra
deps). Lists every scenario - recorded and agent-authored alike, since both
live in ~/.superqa/scenarios - grouped by site, with Run / Run-headless
buttons, live progress, run history, and inline report viewing.

  superqa serve            # http://127.0.0.1:8760
  superqa serve --port 9000 --open

State is shared with the TUI and CLI because all three read the same scenario
folder and the same SQLite store.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .diff import compute_diff, format_diff_lines
from .engine import Engine
from .report import write_index, write_reports
from .scenario import broken_scenarios, find_scenario, list_scenarios, superqa_home
from .store import Store


class RunManager:
    """Runs scenarios in background threads; exposes live status for polling."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def start(self, scenario_name: str, headless: bool) -> str:
        with self._lock:
            self._counter += 1
            token = f"r{self._counter}"
            self._runs[token] = {
                "token": token, "scenario": scenario_name, "status": "running",
                "steps": [], "effects": 0, "report": None, "started": time.time(),
                "current": "", "diff": [],
            }
        threading.Thread(target=self._run, args=(token, scenario_name, headless),
                         daemon=True).start()
        return token

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._runs.values()]

    def _update(self, token: str, **fields) -> None:
        with self._lock:
            if token in self._runs:
                self._runs[token].update(fields)

    def _append_step(self, token: str, entry: dict) -> None:
        with self._lock:
            if token in self._runs:
                self._runs[token]["steps"].append(entry)

    def _run(self, token: str, name: str, headless: bool) -> None:
        store = Store()  # fresh connection: sqlite is not shared across threads
        try:
            sc = find_scenario(name)
        except Exception as e:
            self._update(token, status="error", current=str(e))
            return

        def on_event(ev: dict) -> None:
            kind = ev.get("kind")
            if kind == "step_start":
                self._update(token, current=ev.get("description") or ev.get("action"))
            elif kind == "step_end":
                self._append_step(token, {
                    "index": ev["index"], "status": ev["status"],
                    "error": ev.get("error", "")})
            elif kind == "effect" and ev.get("severity") in ("error", "warning"):
                with self._lock:
                    if token in self._runs:
                        self._runs[token]["effects"] += 1

        run_id = store.start_run(sc.name, sc.site)
        engine = Engine(store=store, headed=not headless, on_event=on_event)
        try:
            result = asyncio.run(engine.run_scenario(sc))
        except Exception as e:
            self._update(token, status="error", current=str(e))
            store.finish_run(run_id, "error", 0, 0, 0, None)
            return
        summary = result.summary_dict()
        prev = store.previous_run(sc.name, run_id)
        diff_lines = format_diff_lines(
            compute_diff(prev.get("summary") if prev else None, summary))
        _, html_p = write_reports(result, store, diff_lines)
        store.finish_run(run_id, result.status, result.passed, result.failed,
                         len(result.visible_effects), str(html_p),
                         json.dumps(summary, ensure_ascii=False))
        write_index(store)
        self._update(token, status=result.status, current="",
                     effects=len(result.visible_effects),
                     report=str(html_p), diff=diff_lines,
                     passed=result.passed, failed=result.failed,
                     total=len(result.step_results))


_MANAGER = RunManager()


def _report_root() -> Path:
    return superqa_home() / "reports"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args) -> None:  # silence default stderr logging
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self._state())
        elif path.startswith("/report/"):
            self._serve_report(unquote(path[len("/report/"):]))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            data = {}
        name = str(data.get("scenario", "")).strip()
        if not name:
            self._json({"error": "scenario required"})
            return
        token = _MANAGER.start(name, headless=bool(data.get("headless", True)))
        self._json({"token": token})

    def _state(self) -> dict:
        scenarios = [
            {"name": s.name, "site": s.site, "steps": len(s.steps),
             "tags": s.tags, "base_url": s.base_url}
            for s in list_scenarios()
        ]
        store = Store()
        runs = store.recent_runs(20)
        now = time.time()
        for r in runs:
            # a DB row still 'running' with no finish and long past its start is a
            # process that died mid-run (e.g. an interrupted sweep) - don't show it
            # as live forever.
            if r.get("status") == "running" and not r.get("finished_at") \
                    and now - r.get("started_at", now) > 300:
                r["status"] = "interrupted"
            if r.get("report_path"):
                rel = _rel_report(r["report_path"])
                if rel:
                    r["report_url"] = "/report/" + quote(rel)
        broken = [{"path": str(p), "error": e} for p, e in broken_scenarios()]
        return {"scenarios": scenarios, "runs": runs,
                "active": _MANAGER.snapshot(), "broken": broken}

    def _serve_report(self, rel: str) -> None:
        root = _report_root().resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            self._send(403, b"forbidden", "text/plain")
            return
        if not target.exists() or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = "text/html; charset=utf-8" if target.suffix == ".html" else (
            "image/png" if target.suffix == ".png" else "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


def _rel_report(report_path: str) -> str:
    try:
        return str(Path(report_path).resolve().relative_to(_report_root().resolve()))
    except Exception:
        return ""


def serve(host: str = "127.0.0.1", port: int = 8760, open_browser: bool = False) -> None:
    superqa_home()  # ensure dirs exist
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"SuperQA 웹 admin: {url}  (Ctrl+C 로 종료)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SuperQA Admin</title>
<style>
:root{--bg:#0b1220;--panel:#16213a;--line:#243250;--text:#e6edf7;--muted:#9fb0c7;
  --accent:#34d399;--fail:#f87171;--warn:#fbbf24}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
  background:var(--bg);color:var(--text);line-height:1.5}
header{padding:16px 24px;border-bottom:1px solid var(--line);display:flex;
  align-items:center;justify-content:space-between}
h1{font-size:19px}h1 span{color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.wrap{display:grid;grid-template-columns:1.15fr .85fr;gap:0;height:calc(100vh - 66px)}
@media(max-width:820px){.wrap{grid-template-columns:1fr;height:auto}}
.col{padding:18px 22px;overflow-y:auto}
.col.left{border-right:1px solid var(--line)}
h2{font-size:14px;color:var(--accent);margin:6px 0 12px;text-transform:uppercase;
  letter-spacing:.04em}
.site{color:var(--muted);font-size:12px;margin:16px 0 6px;font-weight:700}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;margin-bottom:8px}
.card .row{display:flex;align-items:center;justify-content:space-between;gap:10px}
.name{font-weight:600;font-size:14px}
.meta{color:var(--muted);font-size:12px}
.btns{display:flex;gap:6px}
button{border:0;border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer;
  background:#2b3a57;color:var(--text);font-weight:600}
button:hover{background:#374a6d}
button.run{background:var(--accent);color:#04241a}button.run:hover{background:#4ade80}
button:disabled{opacity:.5;cursor:default}
.status{font-size:12px;margin-top:8px;color:var(--muted)}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700}
.b-pass{background:#0f3d2e;color:#5eead4}.b-fail{background:#4a1d1d;color:#fca5a5}
.b-run{background:#3a331a;color:#fcd34d}.b-error{background:#4a1d1d;color:#fca5a5}
.steps{display:flex;gap:3px;flex-wrap:wrap;margin-top:8px}
.dot{width:14px;height:14px;border-radius:4px;background:#2b3a57;font-size:9px;
  display:flex;align-items:center;justify-content:center;color:#8aa}
.dot.pass{background:#0f3d2e}.dot.fail{background:#4a1d1d}.dot.skipped{background:#3a331a}
.run-row{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:8px 12px;margin-bottom:6px;font-size:13px;display:flex;
  justify-content:space-between;align-items:center;gap:8px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.diff{font-size:12px;color:var(--muted);margin-top:6px;white-space:pre-wrap}
.empty{color:var(--muted);font-size:13px;padding:12px 0}
.warnbox{background:#3a331a;border:1px solid #6b5a1a;border-radius:8px;padding:8px 12px;
  font-size:12px;color:#fcd34d;margin-bottom:10px}
#viewer{width:100%;height:100%;border:0;background:#fff;border-radius:8px}
.viewer-wrap{display:none;height:100%}
.viewer-wrap.on{display:block}
.hint{color:var(--muted);font-size:12px;margin-top:4px}
.head-actions{display:flex;gap:8px;align-items:center}
</style></head>
<body>
<header>
  <div><h1>Super<span>QA</span> Admin</h1>
    <div class="sub">녹화 시나리오와 자동 생성 시나리오를 클릭으로 실행 · TUI와 동일한 데이터</div></div>
  <div class="head-actions">
    <label class="sub"><input type="checkbox" id="headless" checked> headless(창 없이)</label>
    <button onclick="load()">새로고침</button>
  </div>
</header>
<div class="wrap">
  <div class="col left">
    <h2>시나리오</h2>
    <div id="broken"></div>
    <div id="scenarios"><div class="empty">불러오는 중...</div></div>
  </div>
  <div class="col right">
    <h2>실행 결과</h2>
    <div id="active"></div>
    <div id="runs"></div>
    <div class="viewer-wrap" id="viewerWrap">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span class="sub">리포트</span>
        <button onclick="closeViewer()">닫기</button>
      </div>
      <iframe id="viewer"></iframe>
    </div>
  </div>
</div>
<script>
let polling = false;
async function api(path, opts){ const r = await fetch(path, opts); return r.json(); }

async function run(name, ev){
  ev.stopPropagation();
  const headless = document.getElementById('headless').checked;
  await api('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({scenario:name, headless})});
  startPolling();
}

function badge(status){
  const map={pass:'b-pass',fail:'b-fail',running:'b-run',error:'b-error',interrupted:'b-error'};
  const label={pass:'성공',fail:'실패',running:'실행 중',error:'오류',interrupted:'중단됨'}[status]||status;
  return `<span class="badge ${map[status]||''}">${label}</span>`;
}

function renderScenarios(scenarios){
  const el = document.getElementById('scenarios');
  if(!scenarios.length){ el.innerHTML='<div class="empty">시나리오가 없습니다. TUI에서 n(기록) 또는 에이전트로 생성하세요.</div>'; return; }
  const bySite={};
  scenarios.forEach(s=>{ (bySite[s.site]=bySite[s.site]||[]).push(s); });
  let html='';
  Object.keys(bySite).sort().forEach(site=>{
    html+=`<div class="site">${site}</div>`;
    bySite[site].forEach(s=>{
      html+=`<div class="card"><div class="row">
        <div><div class="name">${esc(s.name)}</div>
        <div class="meta">${s.steps}단계 ${s.tags.length?'· '+s.tags.join(','):''}</div></div>
        <div class="btns">
          <button class="run" onclick='run(${JSON.stringify(s.name)}, event)'>실행</button>
        </div></div></div>`;
    });
  });
  el.innerHTML=html;
}

function renderBroken(broken){
  const el=document.getElementById('broken');
  el.innerHTML = broken.length ? broken.map(b=>
    `<div class="warnbox">읽을 수 없는 시나리오: ${esc(b.path.split('/').pop())} — ${esc(b.error)}</div>`).join('') : '';
}

function renderActive(active){
  const el=document.getElementById('active');
  const running=active.filter(r=>r.status==='running');
  if(!running.length){ el.innerHTML=''; return; }
  el.innerHTML = running.map(r=>{
    const dots=(r.steps||[]).map(s=>`<div class="dot ${s.status}">${s.index+1}</div>`).join('');
    return `<div class="run-row" style="flex-direction:column;align-items:stretch">
      <div style="display:flex;justify-content:space-between">
        <b>${esc(r.scenario)}</b> ${badge('running')}</div>
      <div class="status">${esc(r.current||'시작 중...')}</div>
      <div class="steps">${dots}</div></div>`;
  }).join('');
}

function renderRuns(runs){
  const el=document.getElementById('runs');
  if(!runs.length){ el.innerHTML='<div class="empty">아직 실행 기록이 없습니다.</div>'; return; }
  el.innerHTML = runs.map(r=>{
    const when=new Date(r.started_at*1000).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    const rep = r.report_url ? `<a href="#" onclick='viewReport(${JSON.stringify(r.report_url)});return false'>리포트</a>` : '';
    return `<div class="run-row"><div>${badge(r.status)}
      <b>${esc(r.scenario)}</b>
      <span class="meta">${when} · ${r.passed}/${r.passed+r.failed} · 부작용 ${r.effects}</span></div>
      ${rep}</div>`;
  }).join('');
}

function viewReport(url){
  document.getElementById('viewer').src=url;
  document.getElementById('viewerWrap').classList.add('on');
}
function closeViewer(){
  document.getElementById('viewerWrap').classList.remove('on');
  document.getElementById('viewer').src='about:blank';
}
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function load(){
  const st=await api('/api/state');
  renderBroken(st.broken||[]);
  renderScenarios(st.scenarios||[]);
  renderActive(st.active||[]);
  renderRuns(st.runs||[]);
  const stillRunning=(st.active||[]).some(r=>r.status==='running');
  if(!stillRunning) polling=false;
  return stillRunning;
}
function startPolling(){
  if(polling) return; polling=true;
  (async function tick(){
    const running=await load();
    if(running || polling){ setTimeout(tick, 1200); } else { polling=false; }
  })();
}
load();
setInterval(()=>{ if(!polling) load(); }, 5000);
</script>
</body></html>"""
