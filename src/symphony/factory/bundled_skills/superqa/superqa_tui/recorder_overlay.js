// SuperQA recorder overlay - injected via add_init_script on every page.
// Records user clicks/inputs as scenario steps and shows a floating panel
// (shadow DOM, so site CSS cannot break it). Talks to Python through the
// context-exposed binding window.__superqa_emit(payload).
(() => {
  if (window.__superqa_installed) return;
  window.__superqa_installed = true;

  const emit = (payload) => {
    try {
      if (window.__superqa_emit) window.__superqa_emit(payload);
    } catch (e) { /* binding gone (page closing) - ignore */ }
  };

  const state = { recording: true, assertNext: false, count: 0 };

  // ---- selector inference ---------------------------------------------------
  const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([^\w-])/g, "\\$1");

  function accessibleName(el) {
    const aria = el.getAttribute && (el.getAttribute("aria-label") || "");
    if (aria) return aria.trim();
    const text = (el.innerText || el.value || el.placeholder || "").trim();
    return text.split("\n")[0].slice(0, 40);
  }

  function roleOf(el) {
    const tag = el.tagName ? el.tagName.toLowerCase() : "";
    const explicit = el.getAttribute && el.getAttribute("role");
    if (explicit) return explicit;
    if (tag === "a" && el.href) return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const t = (el.type || "text").toLowerCase();
      if (["button", "submit", "reset"].includes(t)) return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      return "textbox";
    }
    return "";
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`#${cssEscape(node.id)}`); break; }
      const cls = (node.className && typeof node.className === "string")
        ? node.className.trim().split(/\s+/).filter(c => c && c.length < 30).slice(0, 2) : [];
      if (cls.length) part += "." + cls.map(cssEscape).join(".");
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  }

  function selectorFor(el) {
    const sel = {};
    const testid = el.getAttribute && (el.getAttribute("data-testid") || el.getAttribute("data-test-id"));
    if (testid) sel.testid = testid;
    if (el.id && !/^\d|^(ember|react|vue|:r)/.test(el.id)) sel.css = `#${cssEscape(el.id)}`;
    const role = roleOf(el);
    const name = accessibleName(el);
    if (role && name) { sel.role = role; sel.name = name; }
    if (!sel.testid && !sel.css) sel.css = cssPath(el);
    if (name && !sel.role) sel.text = name;
    return sel;
  }

  function interactive(el) {
    return el.closest && el.closest("a,button,input,select,textarea,[role='button'],[role='link'],[role='tab'],[role='menuitem'],[onclick],label,summary");
  }

  // ---- event capture ----------------------------------------------------------
  function onClick(ev) {
    if (!state.recording) return;
    if (ev.composedPath().some(n => n && n.id === "superqa-host")) return;
    const target = interactive(ev.target) || ev.target;
    if (!target || target.nodeType !== 1) return;
    const sel = selectorFor(target);
    const label = sel.name || sel.text || sel.testid || sel.css || "요소";
    if (state.assertNext) {
      state.assertNext = false;
      ev.preventDefault(); ev.stopPropagation();
      emit({ kind: "step", action: "expect_visible", selector: sel,
             description: `확인: '${label}' 표시됨` });
      bump(`확인 추가: ${label}`);
      return;
    }
    const tag = target.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return; // fill/change handles these
    emit({ kind: "step", action: "click", selector: sel, description: `'${label}' 클릭` });
    bump(`클릭: ${label}`);
  }

  function onChange(ev) {
    if (!state.recording) return;
    const el = ev.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    const sel = selectorFor(el);
    const label = sel.name || el.name || el.placeholder || "입력칸";
    if (tag === "select") {
      emit({ kind: "step", action: "select", selector: sel, value: el.value,
             description: `'${label}' 선택: ${el.value}` });
      bump(`선택: ${label}`);
      return;
    }
    if (tag === "input" || tag === "textarea") {
      const isPw = (el.type || "").toLowerCase() === "password";
      emit({ kind: "step", action: "fill", selector: sel,
             value: isPw ? "{{password}}" : el.value,
             description: isPw ? `'${label}'에 비밀번호 입력` : `'${label}'에 '${el.value}' 입력` });
      bump(`입력: ${label}`);
    }
  }

  function onKeydown(ev) {
    if (!state.recording || ev.key !== "Enter") return;
    const el = ev.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag !== "input" && tag !== "textarea") return;
    emit({ kind: "step", action: "press", selector: selectorFor(el), value: "Enter",
           description: "Enter 키 입력" });
    bump("Enter");
  }

  document.addEventListener("click", onClick, true);
  document.addEventListener("change", onChange, true);
  document.addEventListener("keydown", onKeydown, true);

  // ---- floating panel -----------------------------------------------------------
  let statusEl = null;
  function bump(msg) {
    state.count += 1;
    if (statusEl) statusEl.textContent = `${state.count}단계 기록됨 - ${msg}`;
  }

  function mountPanel() {
    if (!document.body || document.getElementById("superqa-host")) return;
    const host = document.createElement("div");
    host.id = "superqa-host";
    host.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        .panel{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#111827;
          color:#f9fafb;border-radius:12px;padding:10px 12px;box-shadow:0 8px 24px rgba(0,0,0,.35);
          display:flex;flex-direction:column;gap:8px;min-width:230px;font-size:13px}
        .row{display:flex;gap:6px}
        button{flex:1;border:0;border-radius:8px;padding:7px 8px;font-size:12px;cursor:pointer;
          background:#374151;color:#f9fafb}
        button:hover{background:#4b5563}
        .rec{background:#dc2626}.rec:hover{background:#ef4444}
        .save{background:#059669}.save:hover{background:#10b981}
        .title{font-weight:700;display:flex;align-items:center;gap:6px}
        .dot{width:8px;height:8px;border-radius:50%;background:#ef4444;animation:blink 1.2s infinite}
        @keyframes blink{50%{opacity:.25}}
        .status{color:#9ca3af;font-size:11px;min-height:14px}
      </style>
      <div class="panel">
        <div class="title"><span class="dot" id="dot"></span>SuperQA 기록 중</div>
        <div class="status" id="status">사이트를 평소처럼 클릭하세요</div>
        <div class="row">
          <button id="toggle" class="rec">일시정지</button>
          <button id="assert">검증 추가</button>
        </div>
        <div class="row">
          <button id="save" class="save">저장 후 종료</button>
        </div>
      </div>`;
    statusEl = root.getElementById("status");
    const dot = root.getElementById("dot");
    const toggle = root.getElementById("toggle");
    toggle.addEventListener("click", () => {
      state.recording = !state.recording;
      toggle.textContent = state.recording ? "일시정지" : "기록 재개";
      toggle.className = state.recording ? "rec" : "";
      dot.style.visibility = state.recording ? "visible" : "hidden";
    });
    root.getElementById("assert").addEventListener("click", () => {
      state.assertNext = true;
      statusEl.textContent = "다음에 클릭하는 요소를 '표시됨 확인' 단계로 저장합니다";
    });
    root.getElementById("save").addEventListener("click", () => emit({ kind: "save" }));
    document.body.appendChild(host);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountPanel);
  } else {
    mountPanel();
  }
  // Some SPAs re-render document.body and can drop the panel; re-mount it.
  setInterval(() => {
    if (document.body && !document.getElementById("superqa-host")) mountPanel();
  }, 1500);
})();
