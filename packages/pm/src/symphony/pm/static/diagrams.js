const MAX_TEXT = 10000;
const MAX_NODES = 100;
const MAX_EDGES = 200;
const korean = () => globalThis.window?.i18n?.lang === 'ko';
const T = {
  show: (last, total) => (korean() ? `노드 1–${last} 보기 (전체 ${total}개)` : `Show nodes 1–${last} of ${total}`),
  previous: (size) => (korean() ? `이전 ${size}개` : `Previous ${size}`),
  next: (size) => (korean() ? `다음 ${size}개` : `Next ${size}`),
  showing: (first, last, total) => (korean()
    ? `노드 ${first}–${last} 표시 중 (전체 ${total}개). 이 구간 밖으로 이어지는 연결은 소스에서 확인하세요.`
    : `Showing nodes ${first}–${last} of ${total}. Connections that leave this part are listed in the source.`),
  unsafe: (reason) => (korean() ? `이 구간은 안전하게 그릴 수 없습니다: ${reason}` : `This part cannot be drawn safely: ${reason}`),
};

export function diagramSummary(source) {
  const text = String(source || '').replace(/%%.*$/gm, '').trim();
  const nodes = new Set();
  let edges = 0;
  const isEr = /^\s*erDiagram(?:\s|$)/.test(text);
  const isFlow = /^\s*(?:flowchart|graph)(?:\s|$)/.test(text);
  if (!isFlow && !isEr) {
    return { allowed: false, text: 'Only flowchart and ER diagrams are supported.' };
  }
  const body = text.replace(/^\s*(?:flowchart|graph)(?:\s+(?:TB|TD|BT|RL|LR))?\s*;?/i, '').replace(/^\s*erDiagram\s*;?/i, '');
  for (const raw of body.split(/[\n;]/)) {
    const line = raw.replace(/"(?:[^"\\]|\\.)*"/g, '');
    if (isEr) {
      const relation = line.match(/^\s*([A-Za-z][\w-]*)\s+[|o{}]+--[|o{}]+\s+([A-Za-z][\w-]*)/);
      if (relation) { nodes.add(relation[1]); nodes.add(relation[2]); edges += 1; continue; }
      const entity = line.match(/^\s*([A-Za-z][\w-]*)\s*\{/); if (entity) nodes.add(entity[1]);
    } else {
      const parts = [...line.matchAll(/(?:^|[;&\s])([A-Za-z][\w-]*)(?=\s*(?:\[|\(|\{|-->|---|==>))/g)];
      parts.forEach((part) => nodes.add(part[1]));
      for (const edge of line.matchAll(/(?:-->|---|==>)/g)) { edges += 1; void edge; }
      for (const endpoint of line.matchAll(/(?:-->|---|==>)\s*([A-Za-z][\w-]*)/g)) nodes.add(endpoint[1]);
    }
  }
  if (text.length > MAX_TEXT) return { allowed: false, text: 'Diagram exceeds the 10,000 character safety limit.' };
  return { allowed: true, text: 'Diagram preview', source: text };
}

const FLOW_ID = /(?:^|[;&\s])([A-Za-z][\w-]*)(?=\s*(?:\[|\(|\{|-->|---|==>|$|;|&))/g;
const FLOW_EDGE_END = /(?:-->|---|==>)(?:\|[^|]*\|)?\s*([A-Za-z][\w-]*)/g;
const FLOW_EDGE = /(?:-->|---|==>)/g;
const FLOW_KEYWORDS = new Set(['subgraph', 'end', 'style', 'classDef', 'class', 'linkStyle', 'click', 'direction']);
const SHAPE_CLOSE = { '[': ']', '(': ')', '{': '}', '>': ']' };

function stripQuoted(text) { return text.replace(/"(?:[^"\\]|\\.)*"/g, '""'); }

/** Split on `separator` outside double-quoted strings. */
function splitOutsideQuotes(text, separator) {
  const parts = [];
  let current = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"' && text[i - 1] !== '\\') quoted = !quoted;
    if (char === separator && !quoted) { parts.push(current); current = ''; } else current += char;
  }
  parts.push(current);
  return parts;
}

/** Node shape declarations (`id["label"]`, `id(text)`, `id{x}` …) in a flowchart statement. */
function flowDeclarations(statement) {
  const found = [];
  const pattern = /([A-Za-z][\w-]*)\s*(?=[[({>])/g;
  for (let match = pattern.exec(statement); match; match = pattern.exec(statement)) {
    const start = match.index + match[0].length;
    const open = statement[start];
    const close = SHAPE_CLOSE[open];
    let depth = 0;
    let quoted = false;
    let end = -1;
    for (let i = start; i < statement.length; i += 1) {
      const char = statement[i];
      if (char === '"' && statement[i - 1] !== '\\') quoted = !quoted;
      if (quoted) continue;
      if (char === open && open !== '>') depth += 1;
      else if (char === close) { depth -= 1; if (depth <= 0) { end = i; break; } }
      if (open === '>' && i === start) depth = 1;
    }
    if (end < 0) break;
    if (!FLOW_KEYWORDS.has(match[1])) found.push([match[1], `${match[1]}${statement.slice(start, end + 1)}`]);
    pattern.lastIndex = end + 1;
  }
  return found;
}

function statementIds(statement, isEr) {
  const unquoted = stripQuoted(statement);
  const line = isEr ? unquoted : unquoted.replace(/\|[^|]*\|/g, '');
  if (isEr) {
    const relation = line.match(/^\s*([A-Za-z][\w-]*)\s+[|o{}]+(?:--|\.\.)[|o{}]+\s+([A-Za-z][\w-]*)/);
    if (relation) return [relation[1], relation[2]];
    const entity = line.match(/^\s*([A-Za-z][\w-]*)\s*\{/);
    return entity ? [entity[1]] : [];
  }
  const ids = [];
  for (const match of line.replace(/\[[^\]]*\]|\([^)]*\)|\{[^}]*\}/g, '').matchAll(FLOW_ID)) ids.push(match[1]);
  for (const match of line.matchAll(FLOW_EDGE_END)) ids.push(match[1]);
  return [...new Set(ids)].filter((id) => !FLOW_KEYWORDS.has(id));
}

function statementEdges(statement, isEr, ids) {
  if (isEr) return ids.length === 2 ? 1 : 0;
  return [...stripQuoted(statement).replace(/\|[^|]*\|/g, '').matchAll(FLOW_EDGE)].length;
}

function diagramStatements(source) {
  const text = String(source || '').replace(/%%.*$/gm, '').trim();
  const isEr = /^\s*erDiagram(?:\s|$)/.test(text);
  const header = text.match(/^\s*(?:flowchart|graph)(?:\s+(?:TB|TD|BT|RL|LR))?|^\s*erDiagram/i)?.[0]?.trim() || '';
  const body = text.slice(text.indexOf(header) + header.length);
  const statements = [];
  let block = null;
  for (const raw of body.split(/\n/)) {
    if (block !== null) { block.push(raw); if (/^\s*}\s*$/.test(raw)) { statements.push(block.join('\n')); block = null; } continue; }
    if (isEr && /\{\s*$/.test(raw)) { block = [raw]; continue; }
    for (const part of (isEr ? [raw] : splitOutsideQuotes(raw, ';'))) if (part.trim()) statements.push(part);
  }
  if (block !== null) statements.push(block.join('\n'));
  return { header, isEr, statements };
}

/** Split an over-limit flowchart/ER source into parts of at most `size` nodes
 *  and MAX_EDGES connections. A part keeps only statements whose nodes all
 *  fall inside it; nodes whose labels were declared on a dropped statement are
 *  re-declared with that label. The vendor parser re-checks every part. */
export function diagramWindows(source, size = MAX_NODES) {
  const { header, isEr, statements } = diagramStatements(source);
  const order = [];
  const seen = new Set();
  const declarations = new Map();
  const parsed = statements.map((statement) => {
    const trimmed = statement.trim();
    const keyword = trimmed.split(/\s+/, 1)[0];
    const ids = !isEr && FLOW_KEYWORDS.has(keyword) ? null : statementIds(statement, isEr);
    for (const id of ids || []) if (!seen.has(id)) { seen.add(id); order.push(id); }
    // Mermaid keeps a node's last label, so the last declaration wins.
    if (!isEr && ids) for (const [id, declaration] of flowDeclarations(statement)) declarations.set(id, { declaration, statement });
    return { statement, ids, edges: ids ? statementEdges(statement, isEr, ids) : 0 };
  });
  const groups = [];
  let current = new Set();
  let edges = 0;
  const completedBy = (members, id) => parsed.filter(({ ids }) => ids && ids.includes(id) && ids.every((item) => members.has(item)));
  for (const id of order) {
    const tentative = new Set(current).add(id);
    const added = completedBy(tentative, id).reduce((sum, item) => sum + item.edges, 0);
    if (current.size && (tentative.size > size || edges + added > MAX_EDGES)) {
      groups.push([...current]);
      current = new Set([id]);
      edges = completedBy(current, id).reduce((sum, item) => sum + item.edges, 0);
    } else {
      current = tentative;
      edges += added;
    }
  }
  if (current.size) groups.push([...current]);
  const starts = [];
  groups.reduce((offset, group) => { starts.push(offset); return offset + group.length; }, 0);
  return {
    total: order.length,
    count: groups.length,
    window(index) {
      const members = new Set(groups[index]);
      const kept = parsed.filter(({ ids }) => ids && ids.length && ids.every((id) => members.has(id)));
      const keptText = kept.map((item) => item.statement);
      const keptSet = new Set(keptText);
      const drawn = new Set(kept.flatMap(({ ids }) => ids));
      // Re-declare a node after the kept statements when its final label
      // lives on a statement this part dropped; a bare ID keeps lone nodes.
      const extra = [...members]
        .filter((id) => (declarations.has(id) ? !keptSet.has(declarations.get(id).statement) : !drawn.has(id)))
        .map((id) => declarations.get(id)?.declaration || id);
      return { first: starts[index] + 1, last: starts[index] + groups[index].length, source: [header, ...keptText, ...extra].join('\n') };
    },
  };
}

async function renderInto(target, source, textFallback) {
  const { renderSafeDiagram } = await import('./vendor/diagram-entry.js');
  const result = await renderSafeDiagram(`pm-diagram-${crypto.randomUUID()}`, source);
  const actual = `${result.nodes} nodes · ${result.edges} connections`;
  target.innerHTML = result.svg;
  target.setAttribute('role', 'img');
  target.setAttribute('aria-label', textFallback ? `${textFallback}. ${actual}` : actual);
  target.dataset.diagramSummary = actual;
}

function renderWindowControls(target, source, textFallback, message) {
  const windows = diagramWindows(source);
  const notice = document.createElement('p');
  notice.textContent = `${textFallback || 'Diagram preview'}. ${message}`;
  target.replaceChildren(notice);
  target.removeAttribute('role');
  if (windows.count < 2) return;
  const view = document.createElement('div');
  view.className = 'pm-diagram-window';
  const nav = document.createElement('div');
  nav.className = 'pm-diagram-window-nav';
  const button = (label, onClick) => { const item = document.createElement('button'); item.type = 'button'; item.className = 'btn'; item.textContent = label; item.addEventListener('click', onClick); return item; };
  const show = async (index) => {
    const part = windows.window(index);
    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    status.textContent = T.showing(part.first, part.last, windows.total);
    const canvas = document.createElement('div');
    const controls = [];
    if (index > 0) controls.push(button(T.previous(MAX_NODES), () => show(index - 1)));
    if (index + 1 < windows.count) controls.push(button(T.next(MAX_NODES), () => show(index + 1)));
    nav.replaceChildren(...controls);
    view.replaceChildren(status, canvas);
    try { await renderInto(canvas, part.source, textFallback); }
    catch (error) { canvas.textContent = T.unsafe(error.message || error); }
  };
  nav.append(button(T.show(windows.window(0).last, windows.total), () => show(0)));
  target.append(nav, view);
}

export async function renderDiagram(target, source, textFallback) {
  const summary = diagramSummary(source);
  if (!summary.allowed) { target.textContent = summary.text; return; }
  try {
    await renderInto(target, source, textFallback);
  } catch (error) {
    const message = error.message || 'Visual rendering is unavailable.';
    if (/exceeds safety limit/.test(message)) {
      // Expand on demand: never draw more than one bounded part at a time.
      renderWindowControls(target, source, textFallback, message);
      return;
    }
    target.textContent = `${textFallback || summary.text}. ${message}`;
    target.title = message || 'Diagram rendering failed';
    console.warn('PM diagram rendering failed', error);
  }
}
