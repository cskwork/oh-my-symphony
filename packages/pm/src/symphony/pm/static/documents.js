export function sourceEditPolicy(source) {
  return /\r/.test(String(source || ''))
    ? { editable: false, reason: 'This document uses CRLF or mixed newlines. Use External edit mode to preserve exact bytes.' }
    : { editable: true, reason: '' };
}

// Display-only projection for PM readable views. Removes only complete managed
// metadata at top level (outside fences, quotes and indented code), mirroring
// semantic.py/wbs.py grammar; every other line is returned byte for byte.
const WBS_FORMAT_LINE = /^ {0,3}<!-- symphony-wbs-format: 1 -->\s*$/;
const SEMANTIC_MARKER_LINE = /^ {0,3}<!--\s*symphony:(?:req|scenario|storyboard|contract|decision)=[A-Z]+-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\s*-->\s*$/;
const BLOCK_OPEN_LINE = /^ {0,3}<!-- symphony-(task|group):[A-Z]-[A-Z0-9]+ -->\s*$/;
const BLOCK_CLOSE_LINE = /^ {0,3}<!-- \/symphony-(task|group) -->\s*$/;
const BLOCK_YAML_LINE = /^ {0,3}```yaml symphony-(task|group)\s*$/;
const BLOCK_YAML_CLOSE_LINE = /^```\s*$/;

export function readableSource(source) {
  const text = String(source ?? '');
  const lines = text.split('\n');
  // Pass 1: fence context. topLevel marks lines outside any fence, quote or indent;
  // fenceEnd maps a top-level fence opener to its closing line (-1 when unclosed).
  const topLevel = [], fenceEnd = new Map();
  let fence = null;
  lines.forEach((line, index) => {
    const stripped = line.replace(/^[ \t]+/, '');
    const marker = stripped.match(/^(`{3,}|~{3,})/);
    if (fence) {
      const closing = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
      if (closing && closing[1][0] === fence.char && closing[1].length >= fence.length) { fenceEnd.set(fence.start, index); fence = null; }
      topLevel.push(false); return;
    }
    if (marker) { fence = { char: marker[1][0], length: marker[1].length, start: index }; fenceEnd.set(index, -1); }
    topLevel.push(!marker && !stripped.startsWith('>') && !line.startsWith('    ') && !line.startsWith('\t'));
  });
  // Pass 2: pair complete task/group blocks with a stack, so groups may contain tasks.
  const pairs = new Map(), stack = [];
  lines.forEach((line, index) => {
    if (!topLevel[index]) return;
    const open = line.match(BLOCK_OPEN_LINE), close = line.match(BLOCK_CLOSE_LINE);
    if (open) stack.push({ kind: open[1], index });
    else if (close && stack.at(-1)?.kind === close[1]) pairs.set(stack.pop().index, index);
  });
  // Pass 3: remove S1/S2 lines, paired markers and same-kind YAML fences closed inside their block
  // by an exact triple-backtick line; a longer or indented closer leaves the fence visible.
  const removed = new Set(), enclosing = [];
  lines.forEach((line, index) => {
    if (enclosing.length && enclosing.at(-1).close === index) { enclosing.pop(); removed.add(index); return; }
    if (pairs.has(index)) { enclosing.push({ kind: line.match(BLOCK_OPEN_LINE)[1], close: pairs.get(index) }); removed.add(index); return; }
    if (topLevel[index] && (WBS_FORMAT_LINE.test(line) || SEMANTIC_MARKER_LINE.test(line))) { removed.add(index); return; }
    const yaml = fenceEnd.has(index) && line.match(BLOCK_YAML_LINE);
    const end = yaml ? fenceEnd.get(index) : -1;
    if (yaml && enclosing.at(-1)?.kind === yaml[1] && end > index && end < enclosing.at(-1).close && BLOCK_YAML_CLOSE_LINE.test(lines[end])) {
      for (let item = index; item <= end; item += 1) removed.add(item);
    }
  });
  return removed.size ? lines.filter((_, index) => !removed.has(index)).join('\n') : text;
}

export function renderDocumentEditor({ ui, document, error, draft, onSave, onAssist, onDirty, onDraft, authoring = { effective: true, reasons: [] }, assistance = { effective: true, reasons: [] }, onError = () => {}, translate = (value) => value }) {
  const { el, renderMarkdown } = ui;
  if (error) return el('section', { class: 'pm-error-state', role: 'alert' }, [el('h2', null, 'Document unavailable'), el('p', null, error)]);
  if (!document) return el('div', { class: 'pm-loading', role: 'status' }, 'Loading document…');
  if (document.unsupported_reason) return el('section', { class: 'pm-error-state', role: 'status' }, [el('h2', null, 'Read-only document'), el('p', null, document.unsupported_reason), el('pre', null, document.source || '')]);
  const initial = draft === null ? document.source : draft;
  const editPolicy = sourceEditPolicy(document.source);
  let changed = initial !== document.source;
  onDirty(changed);
  const source = el('textarea', {
    class: 'pm-source-editor',
    'aria-label': `${document.document_id} source`,
    spellcheck: true,
  });
  source.value = initial;
  source.disabled = !editPolicy.editable;
  const preview = el('article', { class: 'pm-document-preview' }, renderMarkdown(readableSource(initial)));
  const sourcePane = el('section', { class: 'pm-editor-pane', hidden: true });
  // The heading stays as the pane's accessible name without pushing content below the fold.
  const reviewPane = el('section', { class: 'pm-preview-pane pm-preview-readable' }, [el('h2', { class: 'sr-only' }, 'Readable view'), preview]);
  const toggle = el('button', { class: 'btn', type: 'button', 'aria-pressed': 'false' }, translate('Edit source'));
  const save = el('button', { class: 'btn btn-primary', type: 'button' }, 'Save draft');
  const assist = el('button', { class: 'btn', type: 'button' }, 'Planning assistant');
  save.disabled = !changed || !authoring.effective || !editPolicy.editable;
  if (!authoring.effective) { save.title = (authoring.reasons || []).join(' '); save.dataset.capability = 'authoring'; }
  else if (!editPolicy.editable) save.title = editPolicy.reason;
  if (!assistance.effective) { assist.disabled = true; assist.title = (assistance.reasons || []).join(' '); assist.dataset.capability = 'assistance'; }
  source.addEventListener('input', () => {
    changed = source.value !== document.source; save.disabled = !changed || !authoring.effective;
    onDirty(changed);
    onDraft?.(source.value);
    preview.replaceChildren(renderMarkdown(readableSource(source.value)));
  });
  save.addEventListener('click', async () => {
    if (!changed || !editPolicy.editable) return;
    save.disabled = true;
    try { if (await onSave(source.value) === false) return; changed = false; onDirty(false); }
    catch (error) { onError(error, save); }
    finally { save.disabled = !changed || !authoring.effective; }
  });
  assist.addEventListener('click', async () => {
    try { await onAssist(source.value); } catch (error) { onError(error, assist); }
  });
  toggle.addEventListener('click', () => {
    const editing = sourcePane.hidden;
    sourcePane.hidden = !editing;
    reviewPane.hidden = editing;
    toggle.textContent = translate(editing ? 'Review document' : 'Edit source');
    toggle.setAttribute('aria-pressed', String(editing));
    if (editing) source.focus();
  });
  sourcePane.append(el('div', { class: 'pm-pane-heading' }, [el('h2', null, 'Source'), el('div', { class: 'pm-actions' }, [assist, save])]));
  if (!editPolicy.editable) sourcePane.append(el('p', { class: 'pm-status-card', role: 'status' }, editPolicy.reason));
  sourcePane.append(source);
  return el('div', { class: 'pm-document-layout' }, [
    el('div', { class: 'pm-view-switcher' }, [toggle, draft !== null && draft !== document.source ? el('span', { class: 'pm-draft-badge' }, 'Recovered local draft') : null]),
    sourcePane, reviewPane,
  ]);
}

export function renderFindings(ui, findings = []) {
  if (!findings.length) return ui.el('p', { class: 'pm-empty' }, 'No findings.');
  return ui.el('ul', { class: 'pm-findings' }, findings.map((finding) =>
    ui.el('li', { class: `pm-finding ${finding.severity}` }, [
      ui.el('strong', null, finding.code),
      ui.el('span', null, finding.message),
    ])
  ));
}
