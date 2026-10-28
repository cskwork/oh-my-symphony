const EDITABLE_FIELDS = [
  'title', 'screen_route', 'actor_goal', 'initial_state', 'ui', 'user_action',
  'system_response', 'success', 'alternate', 'failure', 'requirement_refs', 'design_refs',
];

export function storyboardPatchOperation(frame, values) {
  if (!frame?.storyboard_id) throw new Error('Storyboard identity is required.');
  const fields = {};
  for (const name of EDITABLE_FIELDS) {
    if (!(name in values)) continue;
    fields[name] = name.endsWith('_refs')
      ? (Array.isArray(values[name]) ? values[name] : String(values[name] || '').split('\n').map((item) => item.trim()).filter(Boolean))
      : String(values[name] ?? '');
  }
  if (frame.legacy_combined) {
    const first = ['ui', 'user_action', 'system_response'];
    const second = ['success', 'alternate', 'failure'];
    for (const group of [first, second]) {
      if (group.some((name) => name in fields) && !group.every((name) => name in fields)) {
        throw new Error('Legacy storyboard transitions require all three replacement fields.');
      }
    }
  }
  return { op: 'set_storyboard_fields', storyboard_id: frame.storyboard_id, fields };
}

export function renderStoryboard({ ui, overlay, localize = () => {}, onDirty = () => {}, confirmDiscard = () => true, frames = [], selectedId = '', onSelect, onSave, onClose, authoring = { effective: true, reasons: [] }, onError = () => {} }) {
  const { el } = ui;
  if (!frames.length) return el('section', { class: 'pm-empty-state' }, el('p', null, 'No storyboard frames are available.'));
  const selected = frames.find((frame) => frame.storyboard_id === selectedId);
  const cards = el('ol', { class: 'pm-storyboard-list', 'aria-label': 'Storyboard frames' }, frames.map((frame, index) => {
    const action = el('button', {
      class: `pm-storyboard-card ${selectedId === frame.storyboard_id ? 'selected' : ''}`,
      type: 'button', 'aria-expanded': String(selectedId === frame.storyboard_id), 'data-storyboard-id': frame.storyboard_id,
    }, [
      el('span', { class: 'pm-kicker' }, `${index + 1} · ${frame.storyboard_id}`),
      el('strong', null, frame.title || 'Untitled frame'),
      el('span', null, frame.screen_route || 'No screen or route'),
      el('span', null, `${frame.user_action || frame.ui || 'No action'} → ${frame.system_response || frame.success || 'No response'}`),
      el('small', null, [frame.success, frame.alternate, frame.failure].filter(Boolean).join(' · ') || 'No outgoing states'),
    ]);
    action.addEventListener('click', () => onSelect(frame.storyboard_id, action));
    return el('li', null, action);
  }));
  const result = el('section', { class: 'pm-storyboard-layout' }, cards);
  if (!selected) return result;

  const drawer = el('section', { class: 'pm-task-drawer pm-storyboard-drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': `Edit storyboard ${selected.storyboard_id}` });
  const inputs = {};
  const originals = {};
  const changed = () => Object.keys(inputs).some((name) => inputs[name].value !== originals[name]);
  const labels = {
    title: 'Title', screen_route: 'Screen / route', actor_goal: 'Actor and goal', initial_state: 'Initial state',
    ui: 'UI', user_action: 'User action', system_response: 'System response', success: 'Success', alternate: 'Alternate', failure: 'Failure',
    requirement_refs: 'Requirement references', design_refs: 'Design references',
  };
  for (const name of EDITABLE_FIELDS) {
    const multiline = !['title', 'screen_route'].includes(name);
    const input = el(multiline ? 'textarea' : 'input', { class: multiline ? 'input pm-drawer-textarea' : 'input', 'aria-label': labels[name] });
    const value = name.endsWith('_refs') ? (selected[name] || []).join('\n') : selected[name] || '';
    input.value = value; originals[name] = value;
    input.addEventListener('input', () => onDirty(changed()));
    input.disabled = !authoring.effective;
    inputs[name] = input;
    drawer.append(el('label', { class: 'pm-drawer-field' }, [el('span', null, labels[name]), input]));
  }
  const close = el('button', { class: 'btn', type: 'button' }, 'Cancel');
  const save = el('button', { class: 'btn btn-primary', type: 'button' }, 'Save storyboard');
  if (!authoring.effective) { save.disabled = true; save.title = (authoring.reasons || []).join(' '); }
  close.addEventListener('click', () => overlay.close(drawer));
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const operation = storyboardPatchOperation(selected, Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value])));
      await onSave(operation, save); onDirty(false);
    } catch (error) { onError(error, save); }
    finally { save.disabled = !authoring.effective; }
  });
  drawer.prepend(el('div', { class: 'pm-drawer-heading' }, [el('div', null, [el('span', { class: 'pm-kicker' }, selected.storyboard_id), el('h2', null, selected.title || 'Storyboard frame')]), close]));
  drawer.append(el('div', { class: 'pm-actions' }, save));
  queueMicrotask(() => { localize(drawer); overlay.open(drawer, {
    initialFocus: inputs.title, canClose: () => !changed() || confirmDiscard(),
    onClose: () => { onDirty(false); onClose(close); },
  }); });
  return result;
}
