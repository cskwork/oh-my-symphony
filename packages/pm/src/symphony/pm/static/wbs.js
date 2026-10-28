function byParent(tasks) {
  const map = new Map();
  for (const task of tasks) {
    const key = task.parent_id || '';
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(task);
  }
  for (const rows of map.values()) rows.sort((a, b) => a.order - b.order || a.task_id.localeCompare(b.task_id));
  return map;
}

export function renderWbs({ ui, overlay, view, semanticIndex = [], onNavigateReference = () => {}, onSetField, onPaste, onCommand, onCopy, onSelect, onFilter, onScroll, selectedTask = '', filter = '', scrollTop = 0, canUndo = false, canRedo = false, approved = false, readOnlyReason = '', disclose = null }) {
  const { el } = ui;
  const tasks = view.tasks;
  const root = el('section', { class: 'pm-plan-surface' });
  let selected = selectedTask || null;
  const search = el('input', { class: 'input pm-plan-filter', type: 'search', value: filter, placeholder: L('Filter tasks'), 'aria-label': L('Filter tasks') });
  search.addEventListener('input', () => {
    onFilter(search.value);
    for (const row of tbody?.querySelectorAll?.('tr') || []) row.hidden = !row.innerText.toLowerCase().includes(search.value.toLowerCase());
  });
  const action = (label, command, disabled = false) => {
    const readOnly = Boolean(readOnlyReason && command !== 'copy');
    const button = el('button', { class: 'btn', type: 'button', disabled: disabled || readOnly }, L(label));
    if (readOnly) { button.title = readOnlyReason; button.dataset.capability = 'authoring'; }
    button.addEventListener('click', () => onCommand(command, selected));
    return button;
  };
  const addChild = action('Add child', 'add-child');
  const childHint = el('span', { id: 'pm-add-child-hint', class: 'pm-empty', hidden: true }, L('Select a group to add a child task.'));
  const updateChildAction = () => {
    const leaf = Boolean(tasks.find((task) => task.task_id === selected)?.executable);
    addChild.disabled = Boolean(readOnlyReason) || leaf;
    addChild.title = readOnlyReason || (leaf ? L('Select a group to add a child task.') : '');
    childHint.hidden = !leaf;
    if (leaf) addChild.setAttribute('aria-describedby', 'pm-add-child-hint');
    else addChild.removeAttribute('aria-describedby');
  };
  const selectTask = (id) => { selected = id; onSelect(id); updateChildAction(); };
  updateChildAction();
  const actions = el('div', { class: 'pm-actions', role: 'toolbar', 'aria-label': 'Work breakdown actions' }, [
    action('Add group', 'add-group'), action('Add root', 'add-root'), action('Add sibling', 'add-sibling'), addChild, childHint, action('Indent', 'indent'), action('Outdent', 'outdent'),
    search, action('Move up', 'up'), action('Move down', 'down'), action(approved ? 'Cancel task' : 'Delete', 'delete'), action('Undo', 'undo', !canUndo), action('Redo', 'redo', !canRedo),
    action('Copy TSV', 'copy'),
  ]);
  // An optional host disclosure folds the same actions on narrow screens.
  const toolbar = el('div', { class: 'pm-plan-toolbar' }, [
    el('div', null, [el('h2', null, L('Work breakdown')), el('span', { class: 'pm-count' }, `${view.tasks.filter((task) => task.executable).length} ${L('executable tasks')}`)]),
    disclose ? disclose('wbs', L('Work breakdown actions'), [actions]) : actions,
  ]);
  const table = el('table', { class: 'pm-wbs-table', role: 'treegrid', 'aria-label': 'Implementation work breakdown' });
  table.appendChild(el('thead', null, el('tr', null, ['ID', 'Task', 'Type', 'Owner', 'Priority', 'Estimate'].map((name) => el('th', { scope: 'col' }, L(name))))));
  const tbody = el('tbody');
  const tree = byParent(view.tasks);
  const renderRows = (parentId, depth) => {
    for (const task of tree.get(parentId) || []) {
      const title = el('button', { class: 'pm-task-title', type: 'button', style: `--depth:${depth}` }, task.title);
      const row = el('tr', { tabindex: 0, 'aria-level': depth + 1, 'data-task-id': task.task_id }, [
        el('td', { class: 'pm-id-cell' }, task.task_id),
        el('td', null, title),
        el('td', null, task.kind),
        el('td', null, task.human_owner || '—'),
        el('td', null, task.priority || '—'),
        el('td', null, task.estimate || '—'),
      ]);
      title.addEventListener('click', () => openTaskDrawer(ui, task, tasks, semanticIndex, onNavigateReference, onSetField, readOnlyReason, overlay));
      if (selected === task.task_id) row.classList.add('selected');
      row.addEventListener('click', () => { selectTask(task.task_id); tbody.querySelectorAll('tr').forEach((item) => item.classList.toggle('selected', item === row)); });
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') title.click();
        if (event.key === 'ArrowDown' && row.nextElementSibling) row.nextElementSibling.focus();
        if (event.key === 'ArrowUp' && row.previousElementSibling) row.previousElementSibling.focus();
      });
      tbody.appendChild(row);
      renderRows(task.task_id, depth + 1);
    }
  };
  renderRows('', 0);
  table.appendChild(tbody);
  table.addEventListener('paste', async (event) => {
    const text = event.clipboardData && event.clipboardData.getData('text/plain');
    if (!text || !text.includes('\t')) return;
    event.preventDefault();
    if (readOnlyReason) { ui.announce(readOnlyReason); return; }
    await onPaste(text);
  });
  const scroll = el('div', { class: 'pm-table-scroll' }, table); scroll.scrollTop = scrollTop; scroll.addEventListener('scroll', () => onScroll(scroll.scrollTop), { passive: true });
  root.append(toolbar, scroll);
  root.addEventListener('pm-copy', () => onCopy(view.tasks));
  enhanceWithTabulator(root, view.tasks, ui, semanticIndex, onNavigateReference, onSetField, onPaste, selectTask, filter, search, readOnlyReason, overlay).catch(() => {});
  return root;
}

// Tabulator keeps every constructed table in a static registry until destroy().
// Only one WBS grid is visible at a time, so release the previous one before
// building the next; otherwise each Plan render retains a full 1,000-row table.
let activeTable = null;
export function disposeWbs() {
  if (activeTable) { activeTable.destroy(); activeTable = null; }
}

async function enhanceWithTabulator(root, tasks, ui, semanticIndex, onNavigateReference, onSetField, onPaste, onSelect, filter, search, readOnlyReason, overlay) {
  const [{ TabulatorFull: Tabulator }] = await Promise.all([
    import('./vendor/tabulator.min.mjs'),
    ensureStyles('/static/pm/vendor/tabulator.min.css'),
  ]);
  if (!root.isConnected) return;
  disposeWbs();
  const holder = document.createElement('div');
  holder.className = 'pm-tabulator';
  root.querySelector('.pm-table-scroll').replaceChildren(holder);
  const children = byParent(tasks);
  const nest = (parent = '') => (children.get(parent) || []).map((task) => ({ ...task, _children: nest(task.task_id) }));
  const rowsByElement = new WeakMap();
  const table = new Tabulator(holder, {
    data: nest(), dataTree: true, dataTreeStartExpanded: true,
    index: 'task_id', layout: 'fitColumns', height: '520px',
    movableRows: false, clipboard: false, selectableRows: 1,
    // Tabulator re-runs rowFormatter on the same row element after every tree
    // toggle, so it must stay idempotent: record the row and decorate its tree
    // control, but never add listeners here.
    rowFormatter: (row) => {
      const element = row.getElement(); element.tabIndex = 0;
      rowsByElement.set(element, row);
      // The tree module inserts the control only after rowFormatter returns.
      queueMicrotask(() => describeTreeControl(row));
    },
    columns: [
      { title: L('ID'), field: 'task_id', width: 150 },
      { title: L('Task'), field: 'title', minWidth: 260 },
      { title: L('Type'), field: 'kind', width: 120 },
      { title: L('Owner'), field: 'human_owner', width: 150 },
      { title: L('Priority'), field: 'priority', width: 100 },
      { title: L('Estimate'), field: 'estimate', width: 100 },
    ],
  });
  // One delegated listener per table. Tree keys: Enter/Space on the collapse
  // control toggles it; Left/Right on a parent row collapse/expand it. Neither
  // opens the task drawer. A toggle rebuilds the row's cells, so focus returns
  // to the rebuilt control (or the row) instead of falling to <body>.
  holder.addEventListener('keydown', (event) => {
    const element = event.target.closest?.('.tabulator-row');
    const row = element && rowsByElement.get(element);
    if (!row) return;
    const onControl = Boolean(event.target.closest('.tabulator-data-tree-control'));
    const hasChildren = row.getTreeChildren().length > 0;
    const tree = onControl && (event.key === 'Enter' || event.key === ' ') ? 'toggle'
      : hasChildren && event.key === 'ArrowLeft' ? 'collapse'
        : hasChildren && event.key === 'ArrowRight' ? 'expand' : '';
    if (tree) {
      event.preventDefault(); event.stopPropagation();
      if (tree === 'toggle') row.treeToggle(); else if (tree === 'collapse') row.treeCollapse(); else row.treeExpand();
      const control = describeTreeControl(row);
      (onControl && control ? control : row.getElement()).focus();
      return;
    }
    if (event.key === 'Enter') { event.preventDefault(); onSelect(row.getData().task_id); openTaskDrawer(ui, row.getData(), tasks, semanticIndex, onNavigateReference, onSetField, readOnlyReason, overlay); }
    if (event.key === 'ArrowDown') { event.preventDefault(); element.nextElementSibling?.focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); element.previousElementSibling?.focus(); }
  });
  table.on('rowClick', (_event, row) => { onSelect(row.getData().task_id); openTaskDrawer(ui, row.getData(), tasks, semanticIndex, onNavigateReference, onSetField, readOnlyReason, overlay); });
  const matches = (data, value) => `${data.task_id} ${data.title} ${data.kind}`.toLowerCase().includes(value) || (data._children || []).some((child) => matches(child, value));
  if (filter) table.setFilter((data) => matches(data, filter.toLowerCase()));
  search.addEventListener('input', () => table.setFilter((data) => matches(data, search.value.toLowerCase())));
  holder.addEventListener('paste', async (event) => {
    const text = event.clipboardData && event.clipboardData.getData('text/plain');
    if (text && text.includes('\t')) { event.preventDefault(); if (readOnlyReason) ui.announce(readOnlyReason); else await onPaste(text); }
  }, true);
  activeTable = table;
}

function describeTreeControl(row) {
  const control = row.getElement().querySelector('.tabulator-data-tree-control');
  if (control) { control.setAttribute('role', 'button'); control.setAttribute('aria-label', L('Collapse or expand')); control.setAttribute('aria-expanded', String(row.isTreeExpanded())); }
  return control;
}

function ensureStyles(href) {
  if (document.querySelector(`link[href="${href}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = href; link.onload = resolve; link.onerror = reject;
    document.head.appendChild(link);
  });
}

function openTaskDrawer(ui, task, tasks, semanticIndex, onNavigateReference, onSetField, readOnlyReason = '', overlay) {
  const { el } = ui;
  const close = () => overlay.close(panel);
  const field = (label, key, value, multiline = false) => {
    const input = multiline
      ? el('textarea', { class: 'input pm-drawer-textarea', 'aria-label': L(label) })
      : el('input', { class: 'input', value: value || '', 'aria-label': L(label) });
    if (multiline) input.value = Array.isArray(value) ? value.join('\n') : value || '';
    if (readOnlyReason) { input.disabled = true; input.title = readOnlyReason; input.dataset.capability = 'authoring'; }
    input.addEventListener('change', () => onSetField(task.task_id, key, input.value));
    return el('label', { class: 'pm-drawer-field' }, [el('span', null, L(label)), input]);
  };
  const indexed = new Map(semanticIndex.map((item) => [item.semantic_id, item]));
  const referenceLinks = [...(task.requirement_refs || []), ...(task.storyboard_refs || []), ...(task.design_refs || [])].map((reference) => {
    const item = indexed.get(reference);
    const control = el('button', { class: 'btn pm-reference-link', type: 'button', disabled: !item }, item ? `${reference} · ${item.title}` : `${reference} · ${L('Unresolved')}`);
    if (item) control.addEventListener('click', () => { close(); onNavigateReference(item); });
    return control;
  });
  const panel = el('aside', { class: 'pm-task-drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': `Task ${task.task_id}` }, [
    el('div', { class: 'pm-drawer-heading' }, [el('div', null, [el('span', { class: 'pm-kicker' }, task.task_id), el('h2', null, task.title)]), el('button', { class: 'btn-icon', type: 'button', onClick: close, 'aria-label': L('Close') }, '✕')]),
    field('Title', 'title', task.title),
    field('Type', 'kind', task.kind),
    field('Human owner', 'human_owner', task.human_owner),
    field('Agent override', 'agent_kind_override', task.agent_kind_override),
    field('Priority', 'priority', task.priority),
    field('Target date', 'target_date', task.target_date),
    field('Estimate', 'estimate', task.estimate),
    field('Description', 'description', task.description, true),
    field('Acceptance', 'acceptance', task.acceptance, true),
    field('Verification', 'verification', task.verification, true),
    field('Requirement references', 'requirement_refs', task.requirement_refs, true),
    field('Storyboard references', 'storyboard_refs', task.storyboard_refs, true),
    field('Design references', 'design_refs', task.design_refs, true),
    el('section', { class: 'pm-reference-navigation', 'aria-label': L('Reference navigation') }, [el('span', null, L('Reference navigation')), ...(referenceLinks.length ? referenceLinks : [el('small', null, L('No linked planning items.'))])]),
    dependencyPicker(el, task, tasks, onSetField, readOnlyReason),
    field('Integration impact', 'integration_impact', task.integration_impact, true),
    field('Risk', 'risk', task.risk, true),
    field('Notes', 'notes', task.notes, true),
    el('dl', { class: 'pm-task-meta' }, [el('dt', null, L('Source revision')), el('dd', null, task.task_id)]),
  ]);
  overlay.open(panel);
}

function dependencyPicker(el, task, tasks, onSetField, readOnlyReason = '') {
  const selected = new Map((task.dependencies || []).map((item) => [item.task_id, item]));
  const select = el('select', { class: 'input', multiple: true, size: 6, 'aria-label': L('Dependencies') },
    tasks.filter((item) => item.task_id !== task.task_id).map((item) => el('option', { value: item.task_id, selected: selected.has(item.task_id) }, `${item.task_id} · ${item.title}`)));
  if (readOnlyReason) { select.disabled = true; select.title = readOnlyReason; select.dataset.capability = 'authoring'; }
  select.addEventListener('change', () => onSetField(task.task_id, 'dependencies', [...select.selectedOptions].map((item) => selected.get(item.value) || { task_id: item.value, kind: 'requires_code', justification: null })));
  return el('label', { class: 'pm-drawer-field' }, [el('span', null, L('Dependencies')), select, el('small', null, L('Choose required predecessor tasks.'))]);
}

const KO = { 'Filter tasks': '작업 필터', 'Work breakdown': '작업 분해', 'executable tasks': '실행 작업', Task: '작업', Type: '유형', Owner: '담당자', Priority: '우선순위', Estimate: '예상치', 'Add group': '그룹 추가', 'Add root': '최상위 추가', 'Add sibling': '같은 단계 추가', 'Add child': '하위 추가', Indent: '들여쓰기', Outdent: '내어쓰기', 'Move up': '위로', 'Move down': '아래로', Delete: '삭제', 'Cancel task': '작업 취소', Undo: '실행 취소', Redo: '다시 실행', 'Copy TSV': 'TSV 복사', Close: '닫기', Title: '제목', 'Human owner': '담당자', 'Agent override': '에이전트 지정', 'Target date': '목표 날짜', Description: '설명', Acceptance: '인수 조건', Verification: '검증', 'Requirement references': '요구사항 참조', 'Storyboard references': '스토리보드 참조', 'Design references': '설계 참조', Dependencies: '의존성', 'Integration impact': '통합 영향', Risk: '위험', Notes: '메모', 'Source revision': '소스 리비전', 'Choose required predecessor tasks.': '필수 선행 작업을 선택하세요.', 'Reference navigation': '참조 탐색', 'No linked planning items.': '연결된 기획 항목이 없습니다.', Unresolved: '해결되지 않음', 'Collapse or expand': '접기 또는 펼치기', 'Select a group to add a child task.': '하위 작업을 추가하려면 그룹을 선택하세요.' };
function L(value) { return window.i18n?.lang === 'ko' ? (KO[value] || value) : value; }
