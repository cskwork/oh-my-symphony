export function exchangeControlState(format, workbookAvailability, readOnly = false) {
  const workbookReady = Boolean(workbookAvailability?.effective);
  return {
    formatDisabled: false,
    workbookDisabled: Boolean(readOnly || !workbookReady),
    exportDisabled: Boolean(format === 'xlsx' && !workbookReady),
  };
}

export function validateWorkbookFilename(filename, translate = (value) => value) {
  if (typeof filename !== 'string' || !filename || filename !== filename.trim()
      || filename.includes('\0') || filename.includes('/') || filename.includes('\\')) {
    throw new Error(translate('Choose an XLSX workbook with a plain filename.'));
  }
  const suffix = '.xlsx';
  if (!filename.toLocaleLowerCase('en-US').endsWith(suffix) || filename.length === suffix.length) {
    throw new Error(translate('Only XLSX workbooks are supported.'));
  }
  return filename;
}

export function renderExchangePanel({ ui, onExport, onImport, onApply, workbookAvailability = { effective: true, reasons: [] }, readOnly = false, onError = () => {}, translate = (value) => value }) {
  const { el } = ui;
  const workbookReason = (workbookAvailability.reasons || []).join(' ') || translate('Excel workbook exchange is unavailable.');
  const readOnlyReason = translate('Archived planning history is read-only.');
  const format = el('select', { class: 'input pm-format-select', 'aria-label': translate('Export format') },
    [['xlsx', 'Excel workbook'], ['csv', 'CSV'], ['markdown', 'Markdown'], ['html', 'Print HTML'], ['zip', 'Planning package']].map(([value, label]) => el('option', { value, disabled: value === 'xlsx' && !workbookAvailability.effective }, translate(label))));
  const file = el('input', { type: 'file', accept: '.xlsx', 'aria-label': translate('Choose XLSX workbook') });
  const status = el('div', { class: 'pm-import-status', role: 'status', 'aria-live': 'polite' });
  const importButton = el('button', { class: 'btn', type: 'button' }, translate('Preview import'));
  const workbookDisabled = exchangeControlState(format.value, workbookAvailability, readOnly).workbookDisabled;
  if (workbookDisabled) {
    const reason = readOnly ? readOnlyReason : workbookReason;
    for (const item of [file, importButton]) {
      item.disabled = true; item.title = reason; item.dataset.capability = 'exchange';
    }
    status.textContent = readOnly
      ? translate('Archived planning history is read-only. Exports remain available; workbook import is disabled.')
      : translate('Excel workbook import and export are unavailable. {reason} Other export formats remain available.', { reason: workbookReason });
  }
  importButton.addEventListener('click', async () => {
    if (!file.files || !file.files[0]) { status.textContent = translate('Choose a workbook first.'); return; }
    importButton.disabled = true;
    try {
      validateWorkbookFilename(file.files[0].name, translate);
      const preview = await onImport(file.files[0]);
      preview.apply_operation_id ||= crypto.randomUUID().replaceAll('-', '');
      status.replaceChildren(renderImportPreview(ui, preview, onApply, onError, translate));
    } catch (error) {
      status.textContent = error.message;
      onError(error, importButton);
    } finally { importButton.disabled = workbookDisabled; }
  });
  const exportButton = el('button', { class: 'btn', type: 'button' }, translate('Download export'));
  const syncExportState = () => {
    const unavailable = exchangeControlState(format.value, workbookAvailability, readOnly).exportDisabled;
    exportButton.disabled = unavailable;
    exportButton.title = unavailable ? workbookReason : '';
    if (unavailable) exportButton.dataset.capability = 'exchange'; else delete exportButton.dataset.capability;
  };
  if (!workbookAvailability.effective) format.value = 'markdown';
  format.addEventListener('change', syncExportState);
  syncExportState();
  exportButton.addEventListener('click', async () => {
    try { await onExport(format.value); } catch (error) { onError(error, exportButton); }
  });
  return el('section', { class: 'pm-exchange-panel' }, [
    el('div', null, [el('h2', null, translate('Workbook exchange')), el('p', null, translate('Export a revision-bound workbook or preview a three-way import. Nothing is applied from preview.'))]),
    el('div', { class: 'pm-actions' }, [format, exportButton, file, importButton]),
    status,
  ]);
}

function renderImportPreview(ui, preview, onApply, onError, translate) {
  const { el } = ui;
  const rows = [...(preview.changes || []), ...(preview.conflicts || [])];
  const resolutions = {};
  const apply = el('button', { class: 'btn btn-primary', type: 'button' }, translate('Apply reviewed import'));
  apply.addEventListener('click', async () => {
    apply.disabled = true;
    try { await onApply(preview, resolutions); }
    catch (error) { onError(error, apply); }
    finally { apply.disabled = false; }
  });
  return el('div', { class: 'pm-import-preview' }, [
    el('div', { class: `pm-status-card ${(preview.conflicts || []).length ? 'danger' : 'ready'}` }, [
      el('strong', null, translate('{count} proposed changes', { count: preview.changes.length })),
      el('span', null, translate('{count} conflicts require resolution', { count: preview.conflicts.length })),
    ]),
    el('div', { class: 'pm-table-scroll' }, el('table', { class: 'pm-compact-table' }, [
      el('thead', null, el('tr', null, ['Item', 'Field', 'Base', 'Current', 'Workbook', 'Resolution'].map((name) => el('th', null, translate(name))))),
      el('tbody', null, rows.map((row) => el('tr', { class: (preview.conflicts || []).includes(row) ? 'conflict' : '' }, [
        el('td', null, row.item_id), el('td', null, row.field),
        el('td', null, stringify(row.base)), el('td', null, stringify(row.current)),
        el('td', null, stringify(row.imported)),
        el('td', null, (preview.conflicts || []).includes(row) ? conflictChoice(ui, row, resolutions, translate) : translate('Use workbook')),
      ]))),
    ])),
    apply,
  ]);
}

function conflictChoice(ui, row, resolutions, translate) {
  const key = `${row.item_id}:${row.field}`;
  const select = ui.el('select', { class: 'input', 'aria-label': translate('Resolve {key}', { key }) }, [
    ui.el('option', { value: '' }, translate('Choose…')),
    ui.el('option', { value: 'current' }, translate('Keep current')),
    ui.el('option', { value: 'imported' }, translate('Use workbook')),
  ]);
  select.addEventListener('change', () => { if (select.value) resolutions[key] = select.value; else delete resolutions[key]; });
  return select;
}

function stringify(value) {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}
