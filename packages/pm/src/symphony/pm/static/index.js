import { createPmApi } from './api.js';
import { createPmState } from './state.js';
import { readableSource, renderDocumentEditor, renderFindings } from './documents.js';
import { disposeWbs, renderWbs } from './wbs.js';
import { renderExchangePanel } from './exchange.js';
import { renderDiagram, diagramSummary } from './diagrams.js';
import { renderStoryboard } from './storyboard.js';
import { PLANNING_ACTIONS, planningActionRequest, verificationActions, evidenceProvenance } from './assistance.js';
import { PM_GUIDES } from './guide.js';
import { createPmOverlay } from './overlay.js';

const TABS = [['idea', 'Idea'], ['requirements', 'Requirements'], ['design', 'Design'], ['plan', 'Plan'], ['code', 'Code Files']];
const DESIGN_VIEWS = [['ux', 'UX'], ['storyboard', 'Storyboard'], ['architecture', 'Architecture'], ['erd', 'ERD'], ['api', 'API'], ['integration', 'Integration']];
const PLAN_VIEWS = [['wbs', 'Work breakdown'], ['dependencies', 'Dependencies'], ['source', 'Plan document']];

export function capabilityAvailability(payload, name) {
  const value = declaredAvailability(payload, name);
  // Codex (version or login) is checked up front, so every assistance control
  // is disabled with its reason before a proposal could fail.
  const codex = name === 'assistance' ? unavailableHostTool(payload, 'codex') : '';
  return codex ? { ...value, effective: false, reasons: [...value.reasons, codex] } : value;
}

function declaredAvailability(payload, name) {
  const explicit = payload?.capabilities?.[name];
  if (explicit) return {
    requested: Boolean(explicit.requested),
    effective: Boolean(explicit.effective),
    reasons: Array.isArray(explicit.reasons) ? explicit.reasons.map(String) : [],
    developmentVerification: Boolean(explicit.development_verification),
  };
  const requested = Boolean(payload?.flags?.[name]);
  const effective = payload?.effective_flags?.[name] ?? requested;
  return { requested, effective: Boolean(effective), reasons: [], developmentVerification: false };
}

// The reason a probed host tool is unavailable, or '' when it is available or not probed.
function unavailableHostTool(payload, name) {
  const tool = payload?.host_tools?.[name];
  return tool?.available === false ? String(tool.reason || `${name} is unavailable in this host.`) : '';
}

export function mountArtifacts({ root, projectId, api: hostApi, ui, onDirtyChange }) {
  const api = createPmApi(hostApi, projectId);
  const state = createPmState(onDirtyChange, projectId);
  const history = { undo: [], redo: [] };
  let editingRequirement = null;
  let selectedStoryboardId = '';
  let editedDocumentId = null;
  const overlay = createPmOverlay(root);
  root.classList.add('pm-workspace-host');

  const guard = (message = 'Discard unsaved changes?') => {
    if (!state.dirty) return true;
    if (!window.confirm(pmText(message))) return false;
    if (editedDocumentId) state.clearDraft(editedDocumentId);
    state.setDirty(false); return true;
  };
  const openDialog = (dialog, options = {}) => { localizePm(dialog); return !state.disposed && overlay.open(dialog, options); };
  function captureView() {
    const changeId = state.change?.change_id, revision = state.change?.revision, epoch = state.epoch;
    const tab = state.tab, designView = state.designView, planView = state.planView, panel = overlay.panel;
    return { changeId, revision, current: (checkOverlay = true) => state.isCurrent(epoch)
      && state.change?.change_id === changeId && state.change?.revision === revision
      && state.tab === tab && state.designView === designView && state.planView === planView
      && (!checkOverlay || overlay.panel === panel) };
  }

  const hostAvailability = (name) => capabilityAvailability(state.capabilities, name);
  const archived = () => state.change?.state === 'Archived' || state.change?.workspace_state === 'RELEASED';
  const availability = (name) => archived()
    ? { ...hostAvailability(name), effective: false, reasons: ['Archived planning history is read-only.'] }
    : hostAvailability(name);
  const unavailableReason = (name) => {
    const value = availability(name);
    return value.reasons.join(' ') || `PM ${name} is unavailable in this host.`;
  };
  const limitAction = (item, name) => {
    const value = availability(name);
    if (!value.effective) {
      item.disabled = true;
      item.dataset.capability = name;
      item.title = unavailableReason(name);
      item.setAttribute('aria-describedby', 'pm-capability-status');
    }
    return item;
  };
  const limitHostAction = (item, name) => {
    const value = hostAvailability(name);
    if (!value.effective) {
      item.disabled = true;
      item.dataset.capability = name;
      item.title = value.reasons.join(' ') || `PM ${name} is unavailable in this host.`;
      item.setAttribute('aria-describedby', 'pm-capability-status');
    }
    return item;
  };
  const limitArchivedAction = (item) => {
    if (archived()) { item.disabled = true; item.title = 'Archived planning history is read-only.'; }
    return item;
  };
  // OpenSpec runs on Node. Without it the existing title stays first and the
  // Node reason follows (P6); archived history keeps only its read-only title.
  const limitNodeAction = (item, { keepEnabled = false } = {}) => {
    const reason = archived() ? '' : unavailableHostTool(state.capabilities, 'node');
    if (!reason) return item;
    if (!keepEnabled) item.disabled = true;
    item.title = [item.title, reason].filter(Boolean).join(' ');
    item.setAttribute('aria-describedby', 'pm-capability-status');
    return item;
  };
  const demoActive = () => Boolean(state.capabilities?.demo?.active);
  const safeAction = (handler, source = null) => (...args) => Promise.resolve()
    .then(() => handler(...args))
    .catch((error) => reportActionError(error, source || document.activeElement));

  function reportActionError(error, source = null) {
    if (error?.name === 'AbortError') return;
    const code = error?.code ? `${error.code}: ` : '';
    const detail = error?.code === 'approval_required' && error?.message === 'archive requires the approved revision'
      ? pmText('Archive requires an approved revision. Review and approve this revision, then preview archive again.')
      : error?.message || 'The action failed.';
    const message = `${code}${detail}`;
    const errorHost = overlay.panel || root.querySelector('.pm-body') || root;
    errorHost.querySelector('.pm-action-error')?.remove();
    const alert = ui.el('section', { class: 'banner banner-danger pm-action-error', role: 'alert' }, [
      ui.el('strong', null, 'Action unavailable'), ui.el('span', null, message),
    ]);
    localizePm(alert); errorHost.prepend(alert);
    ui.announce(message, 'error');
    if (source?.isConnected && typeof source.focus === 'function') source.focus({ preventScroll: true });
  }

  function capabilityStatus() {
    const names = ['authoring', 'exchange', 'assistance', 'handoff'];
    const unavailable = names.filter((name) => !availability(name).effective);
    const development = names.filter((name) => availability(name).developmentVerification);
    // Codex is already listed through assistance; Node gates several actions, so it gets its own row.
    const tools = ['node'].map((name) => [name, unavailableHostTool(state.capabilities, name)]).filter(([, reason]) => reason);
    const demo = demoActive();
    if (!demo && !unavailable.length && !development.length && !tools.length) return null;
    const rows = [...unavailable.map((name) => [name, unavailableReason(name)]), ...tools].map(([name, reason]) => ui.el('li', null, [
      ui.el('strong', null, `${capabilityText(name)}: `), ui.el('span', null, reason),
    ]));
    if (demo) rows.unshift(ui.el('li', null, 'This is a disposable demo. Planning actions are unlocked only on this server; no AI worker starts and nothing leaves this computer.'));
    else if (development.length) rows.push(ui.el('li', null, 'Development verification is active for configured actions. Dependency checks still apply, and release gates are not marked passed.'));
    const heading = demo ? 'Demo workspace' : unavailable.length || tools.length ? 'Some planning actions are unavailable' : 'Development verification context';
    // The heading and a short summary share the always-visible native summary; the full list folds at every width.
    const summary = [ui.el('strong', null, heading), ui.el('span', { class: 'pm-status-summary' }, ` · ${pmMessage(demo ? 'pm.status.demoSummary' : 'pm.status.more')}`)];
    return ui.el('section', { id: 'pm-capability-status', class: 'pm-status-card', role: 'status' }, [
      compactDisclosure('status', summary, [ui.el('ul', { class: 'pm-capability-list' }, rows)]),
    ]);
  }

  async function load() {
    const token = state.beginLoad(); wbsVisit += 1;
    const capabilities = await api.capabilities(token.signal);
    if (capabilities.project_id) api.setProjectId(capabilities.project_id);
    const changes = await api.changes(token.signal);
    if (!state.isCurrent(token.epoch)) return;
    state.capabilities = capabilities; state.changes = changes.changes || [];
    const selected = chooseAvailableChange(state.changes, state.change?.change_id, state.requestedChangeId);
    const historyId = directHistoryChangeId(state.changes, state.requestedChangeId);
    let loaded = null;
    if (historyId) {
      try { loaded = await api.change(historyId, token.signal); }
      catch (error) {
        if (error?.name === 'AbortError') throw error;
        if (!['change_not_found', 'workspace_unavailable'].includes(error?.code)) throw error;
      }
    }
    if (!loaded && selected) loaded = await api.change(selected.change_id, token.signal);
    if (loaded) {
      state.change = loaded;
      state.requestedChangeId = loaded.change_id; state.restoreViewState(); state.externalEdit = state.change.workspace_state === 'EXTERNAL_EDIT'; state.updateUrl();
    } else {
      state.change = null; state.requestedChangeId = ''; state.externalEdit = false;
      state.planFilter = ''; state.selectedTask = ''; state.planScroll = 0;
      editingRequirement = null; history.undo = []; history.redo = [];
      state.setDirty(false); state.updateUrl();
    }
    if (!state.isCurrent(token.epoch)) return;
    await hydrate(token); render();
  }

  function requiredDocumentIds() {
    if (!state.change) return [];
    if (state.tab === 'idea') return ['proposal'];
    if (state.tab === 'requirements') return state.change.documents.filter((item) => item.document_id.startsWith('spec:')).map((item) => item.document_id);
    if (state.tab === 'design') return [state.designView === 'ux' || state.designView === 'storyboard' ? 'ux' : 'design'];
    if (state.tab === 'plan' && state.planView === 'source') return ['tasks'];
    return [];
  }

  async function hydrate(existingToken = null) {
    const token = existingToken || state.beginLoad();
    state.documents.clear(); state.baselines.clear(); state.errors.clear(); state.wbs = null; state.tickets = []; state.coverage = null;
    if (!state.change) return;
    const jobs = [];
    if (state.tab === 'plan') jobs.push(api.wbs(state.change.change_id, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.wbs = value; }));
    if (state.tab === 'code') jobs.push(api.managedTickets(state.change.change_id, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.tickets = value.tickets || []; }));
    if (state.tab === 'requirements' || state.tab === 'code') jobs.push(api.coverage(state.change.change_id, state.change.revision, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.coverage = value; }));
    for (const docId of requiredDocumentIds()) jobs.push(
      api.document(state.change.change_id, docId, token.signal)
        .then((value) => { if (state.isCurrent(token.epoch)) state.documents.set(docId, value); })
        .catch((error) => { if (error.name !== 'AbortError' && state.isCurrent(token.epoch)) state.errors.set(docId, error.message); })
    );
    if (state.tab === 'requirements') for (const docId of requiredDocumentIds()) {
      const capability = docId.slice(5);
      jobs.push(api.baseline(state.change.change_id, capability, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.baselines.set(capability, value); }).catch(() => {}));
    }
    await Promise.all(jobs);
  }

  async function refreshChange({ keepOverlay = false } = {}) {
    const changeId = state.change.change_id; const token = state.beginLoad();
    const change = await api.change(changeId, token.signal);
    if (!state.isCurrent(token.epoch) || change.change_id !== changeId) return;
    state.change = change; state.externalEdit = change.workspace_state === 'EXTERNAL_EDIT';
    await hydrate(token); if (state.isCurrent(token.epoch)) render({ keepOverlay });
  }

  async function selectChange(id) {
    if (!guard('Discard unsaved PM edits and switch changes?')) return;
    const previous = {
      change: state.change, requestedChangeId: state.requestedChangeId, dirty: state.dirty,
      externalEdit: state.externalEdit, documents: new Map(state.documents), baselines: new Map(state.baselines),
      errors: new Map(state.errors), wbs: state.wbs, tickets: state.tickets, coverage: state.coverage,
      tab: state.tab, designView: state.designView, planView: state.planView, planFilter: state.planFilter,
      selectedTask: state.selectedTask, planScroll: state.planScroll, editingRequirement, selectedStoryboardId,
      undo: [...history.undo], redo: [...history.redo], url: location.href,
    };
    const token = state.beginLoad(); wbsVisit += 1;
    root.setAttribute('aria-busy', 'true');
    for (const control of root.querySelectorAll('button, input, textarea, select')) control.disabled = true;
    try {
      const change = id ? await api.change(id, token.signal) : null;
      if (!state.isCurrent(token.epoch) || (change && change.change_id !== id)) return;
      state.change = change; state.requestedChangeId = change?.change_id || ''; state.externalEdit = change?.workspace_state === 'EXTERNAL_EDIT';
      state.restoreViewState(); await hydrate(token);
      if (!state.isCurrent(token.epoch)) return;
      state.setDirty(false); state.updateUrl(); history.undo = []; history.redo = []; selectedStoryboardId = ''; render();
    } catch (error) {
      if (!state.isCurrent(token.epoch)) return;
      state.change = previous.change; state.requestedChangeId = previous.requestedChangeId; state.externalEdit = previous.externalEdit;
      state.documents = previous.documents; state.baselines = previous.baselines; state.errors = previous.errors;
      state.wbs = previous.wbs; state.tickets = previous.tickets; state.coverage = previous.coverage;
      state.tab = previous.tab; state.designView = previous.designView; state.planView = previous.planView;
      state.planFilter = previous.planFilter; state.selectedTask = previous.selectedTask; state.planScroll = previous.planScroll;
      editingRequirement = previous.editingRequirement; selectedStoryboardId = previous.selectedStoryboardId;
      history.undo = previous.undo; history.redo = previous.redo; state.setDirty(previous.dirty);
      window.history.replaceState(null, '', previous.url); render();
      queueMicrotask(() => root.querySelector('.pm-change-select')?.focus({ preventScroll: true }));
      throw error;
    } finally {
      if (state.isCurrent(token.epoch)) root.removeAttribute('aria-busy');
    }
  }

  async function selectTab(tab) {
    if (tab === state.tab || !guard('Discard unsaved changes and leave this tab?')) return;
    state.setDirty(false); state.setTab(tab); wbsVisit += 1;
    const token = state.beginLoad(); await hydrate(token);
    if (state.isCurrent(token.epoch)) render();
  }

  async function selectSubview(kind, value) {
    if (!guard()) return;
    if (kind === 'design') state.setDesignView(value); else state.setPlanView(value);
    wbsVisit += 1; const token = state.beginLoad(); await hydrate(token);
    if (state.isCurrent(token.epoch)) render();
  }

  function render({ keepOverlay = false } = {}) {
    if (state.disposed) return;
    if (!keepOverlay) overlay.dispose();
    disposeWbs();
    const active = document.activeElement;
    const focusedSummary = root.contains(active) && active.matches?.('details.pm-more > summary') ? active.parentElement.className.match(/\bpm-more-(\w+)/)?.[1] : '';
    planActions = null; compactMoves = [];
    root.replaceChildren(buildHeader(), buildTabs(), buildBody());
    placePlanActions(); placeCompactMoves();
    localizePm(root);
    revealActiveTab();
    if (!keepOverlay) root.querySelector('[aria-current="page"]')?.focus({ preventScroll: true });
    // A focused compact summary keeps focus on its rebuilt counterpart after the default tab focus.
    const summary = focusedSummary && !keepOverlay ? root.querySelector(`details.pm-more-${focusedSummary} > summary`) : null;
    if (summary && shown(summary)) summary.focus({ preventScroll: true });
  }

  // Secondary chrome starts closed at every width; only the user's own toggles open it,
  // and that intent survives renders and breakpoint changes.
  const compactMedia = window.matchMedia?.('(max-width: 560px)') || null;
  const compactOpen = new Set();
  let planActions = null;
  function compactDisclosure(key, summary, children) {
    const details = ui.el('details', { class: `pm-more pm-more-${key}` }, [ui.el('summary', null, summary), ...children]);
    details.open = compactOpen.has(key);
    details.addEventListener('toggle', () => {
      if (details.open) compactOpen.add(key); else compactOpen.delete(key);
    });
    return details;
  }

  // Archive/handoff live inside the WBS disclosure at every width; without one they precede the WBS.
  function placePlanActions() {
    if (!planActions?.row.isConnected) return;
    const { row, wbsNode } = planActions;
    const summary = wbsNode.querySelector('details.pm-more-wbs > summary');
    if (summary) { if (row.previousElementSibling !== summary) summary.after(row); }
    else if (row.nextElementSibling !== wbsNode) wbsNode.before(row);
  }

  // Secondary document controls and tab help fold into the revision disclosure at every
  // width. Hosts without matchMedia (no responsive layout) keep the same nodes in their original slots.
  let compactMoves = [];
  function compactMove(node, restore) { if (node) compactMoves.push({ node, restore }); }
  function placeCompactMoves() {
    const host = root.querySelector('details.pm-more-revision');
    for (const { node, restore } of compactMoves) {
      if (compactMedia && host) { if (node.parentElement !== host) host.append(node); }
      else if (node.parentElement === host) restore(node);
    }
  }

  // A closed native details renders only its first summary, whatever rectangles layout
  // still reports, so every closed ancestor is checked before rectangles count.
  const firstSummary = (details) => [...details.children].find((item) => item.matches('summary')) || null;
  function closedOwner(node) {
    let owner = null;
    for (let child = node, parent = node?.parentElement; parent; child = parent, parent = parent.parentElement) {
      if (parent.matches('details') && !parent.open && child !== firstSummary(parent)) owner = parent;
    }
    return owner;
  }
  const shown = (node) => Boolean(node?.isConnected) && !closedOwner(node) && (typeof node.getClientRects !== 'function' || node.getClientRects().length > 0);
  // Moves focus only when the breakpoint change hid or dropped the owned element that had it.
  function keepCompactFocus(active) {
    if (!active || (shown(active) && document.activeElement === active)) return;
    // The outermost closed ancestor owns the only visible summary on the path.
    const closed = closedOwner(active);
    let target = null;
    if (closed) target = firstSummary(closed);
    else if (active.matches('details.pm-more > summary')) {
      const details = active.parentElement;
      const heading = details.parentElement?.querySelector('h1, h2, strong');
      target = [...details.querySelectorAll('select, input, textarea, button, a[href]')].find((node) => !node.disabled && shown(node))
        || [root.querySelector('.pm-tabs .pm-tab[aria-current="page"]')].find(shown)
        || (shown(heading) ? heading : null);
      // The heading is a programmatic-only fallback and never joins the Tab order.
      if (target === heading) heading.setAttribute('tabindex', '-1');
    } else if (shown(active)) target = active;
    if (target && shown(target)) target.focus({ preventScroll: true });
  }

  function syncCompact() {
    const active = root.contains(document.activeElement) ? document.activeElement : null;
    // Open state is the user's; a width change only re-places owned nodes and repairs hidden focus.
    placePlanActions(); placeCompactMoves();
    keepCompactFocus(active);
  }

  // The rebuilt strip starts at scrollLeft 0, so a narrow screen can clip the active tab.
  // Scroll only the strip, after localization sets label widths; ancestors stay put.
  function revealActiveTab() {
    const nav = root.querySelector('.pm-tabs'), item = nav?.querySelector('[aria-current="page"]')?.getBoundingClientRect?.();
    if (!item) return;
    const box = nav.getBoundingClientRect(), left = box.left + nav.clientLeft, right = Math.min(left + nav.clientWidth, box.right ?? Infinity);
    // scrollLeft moves in whole pixels, so fractional deltas round outward and the tab ends fully inside.
    if (item.left < left) nav.scrollLeft += Math.floor(item.left - left);
    else if (item.right > right) nav.scrollLeft += Math.min(Math.ceil(item.right - right), Math.floor(item.left - left));
  }

  function buildHeader() {
    const selectedId = state.change?.change_id;
    const listed = state.changes.map((item) => (item.change_id === selectedId ? state.change : item));
    const visibleChanges = state.change && !state.changes.some((item) => item.change_id === selectedId) ? [state.change, ...listed] : listed;
    const select = ui.el('select', { class: 'pm-change-select', 'aria-label': pmMessage('pm.header.change') }, [
      ui.el('option', { value: '' }, pmMessage('pm.header.selectChange')),
      ...visibleChanges.map((item) => ui.el('option', { value: item.change_id, selected: state.change?.change_id === item.change_id }, `${item.locator} · ${planningStateText(item.state)}`)),
    ]);
    select.addEventListener('change', () => {
      safeAction(() => selectChange(select.value), select)();
    });
    const create = limitHostAction(button('New change', openCreateDialog, 'btn btn-primary'), 'authoring');
    const repair = limitAction(button('Repair workspace', repairWorkspace), 'authoring');
    const convert = limitHostAction(button('Convert legacy plan', convertLegacy), 'handoff');
    return ui.el('header', { class: 'pm-header' }, [
      ui.el('div', { class: 'pm-heading-copy' }, [ui.el('span', { class: 'pm-kicker' }, 'Product planning'), ui.el('h1', null, 'Artifacts'), ui.el('p', null, 'Shape the idea, align requirements, and hand off executable work.')]),
      compactDisclosure('change', pmMessage('pm.more.change'), [
        ui.el('div', { class: 'pm-header-controls' }, [ui.el('div', { class: 'pm-actions' }, [select, repair, convert, create]), buildGuide('workspace')]),
      ]),
    ]);
  }

  // Collapsed by default; summaries keep help available on demand.
  function buildGuide(area) {
    return ui.el('details', { class: 'pm-guide' }, [
      ui.el('summary', null, 'What do these buttons do?'),
      ui.el('dl', { class: 'pm-guide-list' }, Object.entries(PM_GUIDES[area]).flatMap(([label, text]) => [ui.el('dt', null, label), ui.el('dd', null, text)])),
    ]);
  }

  function buildTabs() {
    return ui.el('nav', { class: 'pm-tabs', 'aria-label': pmMessage('pm.tabs.label') }, TABS.map(([id, label]) => {
      const item = button(label, () => selectTab(id), `pm-tab ${state.tab === id ? 'active' : ''}`);
      if (state.tab === id) item.setAttribute('aria-current', 'page');
      return item;
    }));
  }

  function buildBody() {
    const status = capabilityStatus();
    if (!state.change) return ui.el('main', { class: 'pm-body' }, [status, buildWelcome()]);
    const revisionBar = buildRevisionBar();
    const body = ui.el('main', { class: 'pm-body' }, [status, revisionBar]);
    if (state.externalEdit) {
      body.append(ui.el('section', { class: 'pm-empty-state', role: 'status' }, [ui.el('h2', null, 'External editing is active'), ui.el('p', null, 'Workspace writes are paused. Finish external editing to reindex, validate, and resume here.') ]));
      return body;
    }
    const guide = buildGuide(state.tab);
    body.append(guide);
    if (state.tab === 'plan') body.append(buildPlan());
    else if (state.tab === 'design') body.append(buildDesign());
    else if (state.tab === 'requirements') body.append(buildRequirements());
    else if (state.tab === 'code') body.append(buildCodeFiles());
    else body.append(buildDocument('proposal'));
    compactMove(guide, (node) => revisionBar.after(node));
    return body;
  }

  function buildRevisionBar() {
    const external = state.externalEdit
      ? limitAction(button('Finish external edit & reindex', async () => { const report = await api.endExternal(state.change.change_id); state.externalEdit = false; await refreshChange(); showFindings(report); }), 'authoring')
      : limitAction(button('Edit in external editor', async () => { await api.beginExternal(state.change.change_id, state.change.revision); state.externalEdit = true; render(); ui.announce('App writes paused while external editing is active.'); }), 'authoring');
    // Validate still returns local findings without Node, so it only explains.
    const validate = limitNodeAction(limitArchivedAction(button('Validate', async () => { const view = captureView(); const report = await api.validate(view.changeId, view.revision); if (view.current()) showFindings(report); })), { keepEnabled: true });
    const approve = limitNodeAction(limitAction(button('Approve revision', async () => {
      if (!window.confirm(pmText('Approve this exact planning revision locally?'))) return;
      await api.approve(state.change.change_id, state.change.revision, state.change.approval_confirmation_token); await refreshChange(); ui.announce(pmText('Revision approved.'));
    }, 'btn btn-primary'), 'authoring'));
    // The selected change identity leads the always-visible native summary; only its actions fold, at every width.
    const identity = ui.el('span', { class: 'pm-revision-identity' }, [ui.el('strong', null, state.change.locator), ui.el('span', { class: `pm-state ${state.change.state.toLowerCase()}` }, planningStateText(state.change.state)), ui.el('code', { title: state.change.revision }, state.change.revision)]);
    return ui.el('section', { class: 'pm-revision-bar' }, [
      compactDisclosure('revision', [identity, ui.el('span', { class: 'pm-more-label' }, pmMessage('pm.more.revision'))], [ui.el('div', { class: 'pm-actions' }, [external, validate, approve])]),
    ]);
  }

  function buildDocument(docId) {
    const doc = state.documents.get(docId); const draft = doc ? state.readDraft(docId) : null;
    const editor = renderDocumentEditor({
      ui, document: doc, error: state.errors.get(docId), draft,
      onDirty: (dirty) => { editedDocumentId = docId; state.setDirty(dirty); }, onDraft: (source) => state.writeDraft(docId, source),
      onSave: async (source) => {
        const view = captureView();
        await api.saveDocument(view.changeId, view.revision, docId, source);
        if (!view.current()) return false;
        state.clearDraft(docId); state.setDirty(false); await refreshChange(); ui.announce('Draft saved as a new revision.');
      },
      onAssist: safeAction(async () => openPlanningDialog(docId)),
      authoring: availability('authoring'), assistance: availability('assistance'),
      onError: reportActionError, translate: pmText,
    });
    // The view toggle folds at every width; a recovered-draft notice keeps it in place.
    const switcher = editor.querySelector?.('.pm-view-switcher');
    if (switcher && !switcher.querySelector('.pm-draft-badge')) compactMove(switcher, (node) => editor.prepend(node));
    const kinds = docId === 'proposal' ? ['decision'] : docId === 'ux' ? ['storyboard'] : docId === 'design' ? ['contract'] : docId.startsWith('spec:') ? ['requirement', 'scenario'] : [];
    if (!kinds.length) return editor;
    const actions = kinds.map((kind) => limitAction(button(`Add ${kind}`, async () => {
      if (state.dirty) { ui.announce('Save or discard the current draft before adding a semantic item.'); return; }
      const title = window.prompt(`${kind[0].toUpperCase()}${kind.slice(1)} title`); if (!title) return;
      const result = await api.createSemantic(state.change.change_id, state.change.revision, docId, kind, title);
      await refreshChange(); ui.announce(`Created ${result.impacted_ids?.[0] || kind}.`);
    }), 'authoring'));
    const row = ui.el('div', { class: 'pm-plan-action-row' }, actions);
    const grid = ui.el('div', { class: 'pm-card-grid' }, [row, editor]);
    compactMove(row, (node) => grid.prepend(node));
    return grid;
  }

  function buildRequirements() {
    const ids = requiredDocumentIds();
    if (editingRequirement && ids.includes(editingRequirement)) return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, [button('Back to requirements', () => { if (!guard()) return; editingRequirement = null; render(); })]), buildDocument(editingRequirement)]);
    const create = limitAction(button('Add capability', createCapability, 'btn btn-primary'), 'authoring');
    // Both sides share one projection so the comparison stays like-for-like; delta and <pre> stay raw.
    const readable = (source) => ui.renderMarkdown(readableSource(source));
    if (!ids.length) return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, [create]), empty('No requirement specifications are available for this change.')]);
    const cards = ids.map((id) => {
      const doc = state.documents.get(id);
      if (!doc) return ui.el('section', { class: 'pm-error-state', role: 'alert' }, state.errors.get(id) || `Could not load ${id}`);
      const capability = id.slice(5); const baseline = state.baselines.get(capability);
      const delta = baseline?.exists ? lineDelta(baseline.source, doc.source) : { added: doc.source.split('\n').length, removed: 0 };
      // The authored comparison leads its card; capability/path/delta/Edit follow behind a named summary.
      return ui.el('section', { class: 'pm-readable-card' }, [
        ui.el('div', { class: 'pm-requirement-delta' }, [
          ui.el('article', { class: 'pm-document-preview' }, [ui.el('h3', null, 'Proposed'), readable(doc.source)]),
          ui.el('article', { class: 'pm-document-preview pm-baseline' }, [ui.el('h3', null, baseline?.exists ? 'Current baseline' : pmMessage('pm.requirements.newBaseline')), baseline?.exists ? readable(baseline.source) : ui.el('p', null, pmMessage('pm.requirements.noBaseline'))]),
        ]),
        ui.el('details', { class: 'pm-requirement-meta' }, [
          ui.el('summary', null, pmMessage('pm.requirements.meta', { capability })),
          ui.el('div', { class: 'pm-card-heading' }, [ui.el('div', null, [ui.el('h2', null, capability), ui.el('span', { class: 'pm-count' }, `+${delta.added} / −${delta.removed} lines from baseline`)]), ui.el('div', { class: 'pm-actions' }, [ui.el('code', { title: doc.path }, doc.path), limitArchivedAction(button('Edit requirements', () => { editingRequirement = id; render(); }))])]),
        ]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, 'Source'), ui.el('pre', null, doc.source)]),
      ]);
    });
    const row = ui.el('div', { class: 'pm-plan-action-row' }, [create]);
    const grid = ui.el('div', { class: 'pm-card-grid' }, [row, ...cards, buildCoverage()]);
    compactMove(row, (node) => grid.prepend(node));
    return grid;
  }

  async function createCapability() {
    const capability = window.prompt('Capability identifier (lowercase words with hyphens)'); if (!capability) return;
    const title = window.prompt('Requirement title', capability.replaceAll('-', ' ')); if (!title) return;
    await api.createSpec(state.change.change_id, state.change.revision, capability, title); await refreshChange(); ui.announce(`Created ${capability} requirements.`);
  }

  function buildDesign() {
    const selector = segmented(DESIGN_VIEWS, state.designView, (value) => selectSubview('design', value));
    const docId = state.designView === 'ux' || state.designView === 'storyboard' ? 'ux' : 'design';
    const doc = state.documents.get(docId);
    // The closed summary names the selected view at every width; the selector and Edit source fold behind it.
    // Every read and edit branch puts the readable content first and these tools after it in the same stack.
    const viewName = () => pmText(DESIGN_VIEWS.find(([value]) => value === state.designView)?.[1] || state.designView);
    const tools = compactDisclosure('design', pmMessage('pm.more.design', { view: viewName() }), [selector]);
    // One persistent label names the selected view before the content in every branch; the tools stay after it.
    const current = ui.el('p', { class: 'pm-design-view' }, pmMessage('pm.design.view', { view: viewName() }));
    const surface = ui.el('div', { class: 'pm-design-stack' }, [current]);
    const editSource = (view, id) => {
      const edit = limitArchivedAction(button('Edit source', () => {
        state.setDesignView(view); edit.remove(); firstSummary(tools).textContent = pmMessage('pm.more.design', { view: viewName() });
        current.textContent = pmMessage('pm.design.view', { view: viewName() });
        surface.replaceChildren(current, buildDocument(id), tools); localizePm(surface); placeCompactMoves();
      }));
      tools.append(edit);
    };
    if (!doc) { surface.append(empty(state.errors.get(docId) || 'Loading design document…'), tools); return surface; }
    if (state.designView === 'storyboard') {
      surface.append(renderStoryboard({
        ui, overlay, localize: localizePm, onDirty: state.setDirty, confirmDiscard: () => guard('Discard unsaved storyboard changes?'), frames: state.change.storyboard_frames || [], selectedId: selectedStoryboardId,
        authoring: availability('authoring'), onError: reportActionError,
        onSelect: (id) => { selectedStoryboardId = id; render(); },
        onClose: () => { state.setDirty(false); const prior = selectedStoryboardId; selectedStoryboardId = ''; render(); queueMicrotask(() => root.querySelector(`[data-storyboard-id="${prior}"]`)?.focus()); },
        onSave: async (operation) => {
          await api.patchStoryboard(state.change.change_id, state.change.revision, operation);
          selectedStoryboardId = ''; await refreshChange(); ui.announce('Storyboard frame saved.');
        },
      }), tools);
      editSource('ux', 'ux');
      return surface;
    }
    // Project the full document first so fence context survives section slicing;
    // diagrams still read the raw section.
    const section = extractSection(doc.source, state.designView);
    const readableDoc = readableSource(doc.source);
    const readableSection = extractSection(readableDoc, state.designView);
    surface.append(ui.el('section', { class: 'pm-readable-card' }, [ui.el('article', { class: 'pm-document-preview' }, ui.renderMarkdown(readableSection || readableDoc))]));
    for (const diagram of diagramBlocks(section || doc.source)) {
      const target = ui.el('div', { class: 'pm-diagram', role: 'status' }, diagramSummary(diagram).text);
      surface.append(target, ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, 'Diagram text alternative'), ui.el('pre', null, diagram)]));
      queueMicrotask(() => renderDiagram(target, diagram, diagramSummary(diagram).text));
    }
    surface.append(tools);
    editSource(docId === 'ux' ? 'ux' : 'architecture', docId);
    return surface;
  }

  function buildPlan() {
    const stack = ui.el('div', { class: 'pm-plan-stack' }, [segmented(PLAN_VIEWS, state.planView, (value) => selectSubview('plan', value))]);
    if (state.planView === 'source') { stack.append(buildDocument('tasks')); return stack; }
    if (!state.wbs) { stack.append(empty('Loading plan…')); return stack; }
    if (state.planView === 'dependencies') {
      const label = taskLabeler(state.wbs.tasks);
      const rows = state.wbs.tasks.flatMap((task) => (task.dependencies || []).map((dep) => [label(task.task_id), label(dep.task_id), dependencyKindCell(dep.kind), dep.justification || '—']));
      stack.append(simpleTable(['pm.deps.col.task', 'pm.deps.col.needs', 'pm.deps.col.kind', 'pm.deps.col.reason'].map((key) => pmMessage(key)), rows)); return stack;
    }
    const handoff = limitAction(button('Preview approved handoff', previewHandoff, 'btn btn-primary'), 'handoff');
    const archive = limitNodeAction(limitAction(button('Preview archive', previewArchive), 'handoff'));
    // render() places this same row after commit inside the WBS disclosure at every width.
    const row = ui.el('div', { class: 'pm-plan-action-row' }, [archive, handoff]);
    const wbsNode = renderWbs({ ui, disclose: (key, summary, children) => compactDisclosure(key, key === 'wbs' ? pmMessage('pm.more.planActions') : summary, children), overlay, view: state.wbs, semanticIndex: state.change.semantic_index || [], onNavigateReference: safeAction(navigateSemantic), onSetField: safeAction(setTaskField), onPaste: safeAction(previewPaste), onCommand: safeAction(queueWbsCommand), onCopy: safeAction(copyTsv), onSelect: state.setSelectedTask, onFilter: state.setPlanFilter, onScroll: state.setPlanScroll, selectedTask: state.selectedTask, filter: state.planFilter, scrollTop: state.planScroll, canUndo: history.undo.length > 0, canRedo: history.redo.length > 0, approved: Boolean(state.change.approval_id), readOnlyReason: availability('authoring').effective ? '' : unavailableReason('authoring') });
    stack.append(row, wbsNode);
    planActions = { row, wbsNode };
    stack.append(renderExchangePanel({ ui, onExport: exportPlanning, onImport: importWorkbook, onApply: applyImport, workbookAvailability: hostAvailability('exchange'), readOnly: archived(), onError: reportActionError, translate: pmText }), renderFindings(ui, state.wbs.findings));
    return stack;
  }

  async function navigateSemantic(item) {
    if (!item) return;
    if (item.kind === 'requirement' || item.kind === 'scenario') {
      state.setTab('requirements'); editingRequirement = item.document_id;
    } else if (item.kind === 'storyboard') {
      state.setTab('design'); state.setDesignView('storyboard');
    } else if (item.kind === 'contract') {
      state.setTab('design'); state.setDesignView('api');
    } else {
      state.setTab('idea');
    }
    await hydrate(); render();
  }

  function buildCoverage() {
    if (!state.coverage) return empty('Coverage is loading…');
    const entities = state.coverage.entities || [];
    return ui.el('section', { class: 'pm-readable-card pm-coverage' }, [
      ui.el('h2', null, 'Requirement and scenario coverage'),
      ui.el('p', null, pmMessage('pm.coverage.independent')),
      ...entities.map((entity) => {
        const states = ['specified', 'linked', 'implemented', 'tested', 'accepted', 'waived'];
        const waiverActions = (entity.waivers || []).filter((item) => item.state === 'ACTIVE').map((item) => limitAction(button('Revoke waiver', () => revokeWaiver(item)), 'authoring'));
        return ui.el('details', { class: 'pm-coverage-entity' }, [
          ui.el('summary', null, `${entity.semantic_id} · ${entity.title}`),
          ui.el('div', { class: 'pm-coverage-states' }, states.map((name) => {
            const value = entity.states?.[name] || { satisfied: false, qualified_count: 0, total_count: 0 };
            return ui.el('span', { class: `pm-coverage-state ${value.satisfied ? 'ready' : ''}` }, `${name} · ${value.qualified_count}/${value.total_count}`);
          })),
          simpleTable(['Task', 'Revision', 'Ticket', 'Implemented', 'Tested', 'Accepted', 'Provenance'], (entity.task_links || []).map((link) => [link.task_id, link.task_revision, link.ticket_identifier, link.implemented ? 'Yes' : 'No', link.tested ? 'Yes' : 'No', link.accepted ? 'Yes' : 'No', link.test_provenance || '—'])),
          ...(entity.waivers || []).map((waiver) => ui.el('p', { class: 'pm-waiver' }, `${waiver.state} waiver · ${waiver.reason} · ${waiver.reviewer || 'unknown reviewer'} · ${waiver.revision}`)),
          ui.el('div', { class: 'pm-actions' }, [limitAction(button('Waive coverage', () => openWaiverDialog(entity)), 'authoring'), ...waiverActions]),
        ]);
      }),
      renderFindings(ui, state.coverage.findings || []),
    ]);
  }

  function openWaiverDialog(entity) {
    const reason = ui.el('textarea', { class: 'input pm-drawer-textarea', 'aria-label': 'Waiver reason' });
    const reviewer = ui.el('input', { class: 'input', 'aria-label': 'Reviewer label', value: 'local operator' });
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Waive coverage' });
    const close = () => overlay.close(dialog);
    const preview = button('Preview waiver', async () => {
      const view = captureView();
      const result = await api.waiverPreview(view.changeId, view.revision, entity.semantic_id, reason.value, reviewer.value);
      if (!view.current()) return;
      showConfirmation('Confirm coverage waiver', 'This waiver does not grant execution, acceptance, ancestry, completion, or archive authority.', `${entity.semantic_id}\n${reason.value}\n${state.change.revision}`, 'Confirm waiver', async () => {
        await api.createWaiver(view.changeId, view.revision, { semantic_id: entity.semantic_id, reason: reason.value, reviewer_label: reviewer.value }, result.confirmation_token);
        await refreshChange(); ui.announce('Coverage waiver recorded without granting completion authority.');
      }); close();
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, `Waive ${entity.semantic_id}`), field('Reason', reason), field('Reviewer', reviewer), ui.el('div', { class: 'pm-actions' }, [preview, button('Cancel', close)]));
    openDialog(dialog, { initialFocus: reason });
  }

  async function revokeWaiver(waiver) {
    if (!window.confirm(`Revoke waiver ${waiver.waiver_id}?`)) return;
    await api.revokeWaiver(state.change.change_id, state.change.revision, waiver.waiver_id, 'Revoked after review', 'local operator');
    await refreshChange(); ui.announce('Coverage waiver revoked.');
  }

  function renderEvidenceReceipt(item) {
    const hostExecuted = item.test_provenance === 'host_executed' || item.procedure_kind === 'native';
    const rows = [
      ['Procedure', item.procedure_id], ['Result', item.result], ['Provenance', hostExecuted ? 'Host executed' : 'Manual reported'],
      ['Tested commit', item.tested_commit], ['Receipt', item.receipt_id],
    ];
    if (hostExecuted) rows.push(['Return code', item.return_code ?? 'Unavailable'], ['Timed out', item.timed_out ? 'Yes' : 'No'], ['Stdout hash', item.stdout_hash], ['Stderr hash', item.stderr_hash]);
    else rows.push(['Environment', item.environment_summary || '—'], ['Submitted by', item.submitted_by || '—']);
    return ui.el('article', { class: 'pm-evidence-receipt' }, [ui.el('h4', null, `${item.procedure_id} · ${item.result}`), ui.el('dl', { class: 'pm-task-meta' }, rows.flatMap(([name, value]) => [ui.el('dt', null, name), ui.el('dd', { class: name.includes('hash') || name.includes('commit') ? 'pm-mono' : '' }, value ?? '—')]))]);
  }

  function buildCodeFiles() {
    const files = state.change.documents.map((doc) => [doc.path, documentStateCell(doc.state), pmMessage(doc.editable ? 'pm.files.role.source' : 'pm.files.role.readOnly')]);
    const ticketCards = state.tickets.map((ticket) => {
      const accept = limitAction(button(ticket.acceptance ? 'Accepted' : 'Review verification', () => reviewAcceptance(ticket), 'btn'), 'handoff');
      accept.disabled = accept.disabled || Boolean(ticket.acceptance || !ticket.verified_commit);
      const allowed = ticket.allowed_procedures || (ticket.required_procedure_ids || []).map((procedure_id) => ({ procedure_id, kind: 'native', label: procedure_id }));
      const procedures = verificationActions(allowed).flatMap((procedure) => {
        const noRun = limitAction(button(procedure.label, () => openEvidenceDialog(ticket, procedure)), 'handoff');
        const runBound = procedure.kind === 'native' && ticket.run_id ? limitAction(button(`Verify native run with ${procedure.procedure_id}`, () => runVerification(ticket, procedure.procedure_id)), 'handoff') : null;
        return [noRun, runBound].filter(Boolean);
      });
      const codeRows = (ticket.code_files || []).map((item) => [item.status, item.path]);
      const receipts = ticket.evidence || ticket.receipts || [];
      const artifactLinks = receipts.flatMap((item) => [item.report_ref, ...(item.artifact_refs || [])].filter(Boolean).map((raw) => {
        const ref = typeof raw === 'string' ? { ref: raw } : raw;
        if (ref.download_url) return button(`${item.procedure_id} · ${ref.ref}`, () => ui.downloadArtifact(withProjectIdentity(ref.download_url, state.capabilities?.project_id || state.projectId), ref.ref.split('/').pop()), 'pm-evidence-link');
        const prefix = `artifact://${ticket.ticket_identifier}/`; const name = String(ref.ref || '').startsWith(prefix) ? String(ref.ref).slice(prefix.length) : '';
        return name ? button(`${item.procedure_id} · ${name}`, () => ui.downloadArtifact(`/issues/${encodeURIComponent(ticket.ticket_identifier)}/artifacts/${encodeURIComponent(name)}`, name), 'pm-evidence-link') : null;
      })).filter(Boolean);
      return ui.el('article', { class: 'pm-ticket-card' }, [
        ui.el('div', { class: 'pm-card-heading' }, [ui.el('h3', null, ticket.ticket_identifier), ui.el('span', { class: 'pm-state' }, ticket.state)]),
        ui.el('dl', { class: 'pm-task-meta' }, [ui.el('dt', null, 'Task'), ui.el('dd', null, ticket.task_id), ui.el('dt', null, 'Revision'), ui.el('dd', null, ticket.task_revision), ui.el('dt', null, 'Base commit'), ui.el('dd', { class: 'pm-mono' }, ticket.base_commit || '—'), ui.el('dt', null, 'Head commit'), ui.el('dd', { class: 'pm-mono' }, ticket.head_commit || 'No completed run'), ui.el('dt', null, 'Verified candidate'), ui.el('dd', { class: 'pm-mono' }, ticket.verified_commit || 'No complete passing evidence set'), ui.el('dt', null, 'Accepted commit'), ui.el('dd', { class: 'pm-mono' }, ticket.acceptance?.tested_commit || 'No explicit acceptance')]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, `Code delta · ${codeRows.length} files`), simpleTable(['Status', 'Path'], codeRows)]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, `Verification evidence · ${receipts.length} receipts · ${evidenceProvenance(receipts)}`), ...receipts.map(renderEvidenceReceipt), ...artifactLinks]),
        ui.el('div', { class: 'pm-actions' }, [ticket.head_commit ? button('Review Git commit', () => ui.openGitCommit(ticket.head_commit)) : null, ticket.run_id ? button('Open run evidence', () => ui.openRun(ticket.run_id)) : null, ...procedures, accept]),
      ]);
    });
    return ui.el('div', { class: 'pm-card-grid' }, [
      ui.el('section', { class: 'pm-readable-card' }, [ui.el('h2', null, 'Planning files'), simpleTable(['path', 'state', 'role'].map((key) => pmMessage(`pm.files.col.${key}`)), files)]),
      ui.el('section', { class: 'pm-readable-card' }, [ui.el('h2', null, 'Handoff tickets'), ui.el('p', null, ticketCards.length ? 'Verification and acceptance are bound to the exact task revision and tested commit.' : pmMessage('pm.code.noTickets')), ...ticketCards]),
      buildCoverage(),
    ]);
  }

  function openEvidenceDialog(ticket, procedure) {
    const native = procedure.kind === 'native';
    const testedCommit = ui.el('input', { class: 'input', 'aria-label': 'Tested commit', value: ticket.head_commit || ticket.base_commit || '' });
    const environment = ui.el('textarea', { class: 'input', 'aria-label': 'Environment summary' });
    const result = ui.el('select', { class: 'input', 'aria-label': 'Manual result' }, [ui.el('option', { value: 'passed' }, 'Passed'), ui.el('option', { value: 'failed' }, 'Failed')]);
    const started = ui.el('input', { class: 'input', type: 'datetime-local', 'aria-label': 'Started at' });
    const completed = ui.el('input', { class: 'input', type: 'datetime-local', 'aria-label': 'Completed at' });
    const report = ui.el('input', { class: 'input', 'aria-label': 'Report path' });
    const artifacts = ui.el('textarea', { class: 'input', 'aria-label': 'Artifact references' });
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': native ? 'Run verification' : 'Submit completion evidence' });
    const close = () => overlay.close(dialog);
    const preview = button(native ? 'Preview host verification' : 'Preview evidence', async () => {
      const view = captureView();
      if (!testedCommit.value.trim()) throw new Error('Tested commit is required.');
      if (!native && (!environment.value.trim() || !started.value || !completed.value || !report.value.trim())) throw new Error('Environment, timestamps, and report path are required.');
      if (!native && procedure.artifacts === 'required' && !artifacts.value.trim()) throw new Error('This procedure requires at least one artifact reference.');
      const payload = {
        ticket_identifier: ticket.ticket_identifier, task_revision: ticket.task_revision,
        tested_commit: testedCommit.value.trim(), procedure_id: procedure.procedure_id,
      };
      if (!native) Object.assign(payload, {
        environment_summary: environment.value, result: result.value,
        started_at: new Date(started.value).toISOString(), completed_at: new Date(completed.value).toISOString(),
        report_ref: { ref_kind: 'git_blob', ref: report.value.trim() },
        artifact_refs: artifacts.value.split('\n').map((ref) => ref.trim()).filter(Boolean).map((ref) => ref.startsWith('artifact://') ? { ref_kind: 'artifact_store', ref } : { ref_kind: 'git_blob', ref }),
      });
      const response = await api.evidencePreview(view.changeId, view.revision, payload);
      if (!view.current()) return;
      showConfirmation(native ? 'Confirm host verification' : 'Confirm completion evidence', native ? 'The host will run the approved command and derive the actual result.' : 'This stores inspectable evidence but does not complete or accept the task.', JSON.stringify(response.normalized || payload, null, 2), native ? 'Run approved command' : 'Submit evidence', async () => {
        await api.evidenceSubmit(view.changeId, view.revision, response.normalized || payload, response.confirmation_token);
        await refreshChange(); ui.announce(native ? 'Host verification receipt recorded.' : 'Manual completion evidence recorded. Explicit acceptance is still required.');
      }); close();
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, native ? 'Run verification' : 'Submit completion evidence'), ui.el('p', null, `${procedure.label || procedure.procedure_id} · ${procedure.definition_digest || ''}`), field('Tested commit', testedCommit));
    if (native) dialog.append(ui.el('p', { class: 'pm-empty' }, 'The host derives return code, output hashes, timeout, and result. You cannot report a native pass.'));
    else dialog.append(field('Environment summary', environment), field('Result', result), field(`Started at · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`, started), field(`Completed at · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`, completed), field('Report path at tested commit', report), field('Artifact references', artifacts));
    dialog.append(ui.el('div', { class: 'pm-actions' }, [preview, button('Cancel', close)]));
    openDialog(dialog, { initialFocus: testedCommit });
  }

  async function reviewAcceptance(ticket) {
    const view = captureView();
    const preview = await api.acceptancePreview(ticket.ticket_identifier, ticket.task_revision, ticket.verified_commit);
    if (!view.current()) return;
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Acceptance review' });
    const confirm = button('Accept verified revision', async () => {
      if (!view.current(false)) return;
      await api.accept(ticket.ticket_identifier, ticket.task_revision, ticket.verified_commit, preview.confirmation_token);
      overlay.close(dialog, { force: true }); await refreshChange(); ui.announce('Verified revision accepted.');
    }, 'btn btn-primary');
    confirm.disabled = !preview.ready;
    dialog.append(ui.el('h2', null, preview.ready ? 'Verification is complete' : 'Verification is incomplete'), ui.el('p', null, `${preview.passing_receipt_ids.length} passing receipts · ${preview.missing_procedure_ids.length} missing procedures`), renderFindings(ui, preview.findings), ui.el('div', { class: 'pm-actions' }, [confirm, button('Cancel', () => overlay.close(dialog))]));
    openDialog(dialog);
  }

  async function runVerification(ticket, procedureId) {
    const runId = window.prompt(`Run ID whose checkout should execute ${procedureId}`); if (!runId) return;
    const receipt = await api.verify(state.change.change_id, ticket.ticket_identifier, ticket.task_revision, procedureId, runId);
    ui.announce(`${procedureId}: ${receipt.result}`); await refreshChange();
  }

  async function runWbs(operations, inverse = null, record = true, keepOverlay = false) {
    const result = await api.patchWbs(state.change.change_id, state.change.revision, operations);
    if (record && inverse) { history.undo.push({ operations: inverse, inverse: operations }); history.redo = []; }
    await refreshChange({ keepOverlay });
    return result;
  }

  async function setTaskField(taskId, field, value) {
    const task = state.wbs.tasks.find((item) => item.task_id === taskId); if (!task) return;
    const normalized = ['acceptance', 'verification', 'requirement_refs', 'storyboard_refs', 'design_refs'].includes(field) && typeof value === 'string' ? value.split('\n').map((item) => item.trim()).filter(Boolean) : value;
    const apiField = TASK_TO_API_FIELD[field] || field;
    await runWbs([{ op: 'set_field', task_id: taskId, field: apiField, value: normalized }], [{ op: 'set_field', task_id: taskId, field: apiField, value: task[field] }], true, true);
  }

  // A command pressed while an earlier one is still refreshing would read the outgoing
  // state.wbs; run commands in order so each sees the refreshed tasks and revision.
  // A queued command belongs to the change and visit it was pressed in: a change
  // switch, reload, tab or subview change or unmount drops it, while the queue's own
  // refreshes keep it.
  let wbsCommands = Promise.resolve(), wbsVisit = 0;
  function queueWbsCommand(command, selectedId) {
    const changeId = state.change?.change_id, visit = wbsVisit;
    const owned = () => !state.disposed && visit === wbsVisit && state.change?.change_id === changeId;
    const run = wbsCommands.then(() => (owned() ? commandWbs(command, selectedId) : undefined));
    wbsCommands = run.catch(() => {});
    return run;
  }

  async function commandWbs(command, selectedId) {
    if (command === 'undo' || command === 'redo') {
      const from = command === 'undo' ? history.undo : history.redo, to = command === 'undo' ? history.redo : history.undo;
      const item = from.pop(); if (!item) return; await runWbs(item.operations, null, false); to.push({ operations: item.inverse, inverse: item.operations }); render(); return;
    }
    if (command === 'copy') { await copyTsv(state.wbs.tasks); return; }
    const tasks = state.wbs.tasks; const selected = tasks.find((item) => item.task_id === selectedId);
    if (command === 'add-root' || command === 'add-child' || command === 'add-sibling' || command === 'add-group') {
      const group = command === 'add-group';
      if (command === 'add-sibling' && !selected) { ui.announce('Select a task first.'); return; }
      const parentId = command === 'add-child' ? selectedId : command === 'add-sibling' ? selected.parent_id : null;
      const siblings = tasks.filter((item) => item.parent_id === parentId);
      const task = { parent_id: parentId, order: Math.max(0, ...siblings.map((item) => item.order)) + 10, kind: group ? 'Group' : 'Feature', title: group ? 'New group' : 'New task', description: '', dependencies: [], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [], integration_impact: '', risk: '', notes: '', executable: !group };
      const result = await runWbs([{ op: 'create_task', task }], null, false);
      const id = result.impacted_ids?.[0]; if (!id) throw new Error('Server did not assign a work item identity.');
      const restored = { ...task, task_id: id };
      history.undo.push({ operations: [{ op: 'delete_task', task_id: id }], inverse: [{ op: 'restore_task', task_id: id, task: restored }] }); history.redo = []; render();
      return;
    }
    if (!selected) { ui.announce('Select a task first.'); return; }
    if (command === 'delete' && state.change.approval_id) {
      const reason = window.prompt(`Cancellation reason for ${selected.task_id}`); if (!reason?.trim()) return;
      const raw = window.prompt('Successor task IDs (comma-separated, optional)', '') || '';
      const successorRefs = raw.split(',').map((item) => item.trim()).filter(Boolean);
      if (window.confirm(`Cancel ${selected.task_id} and retain its reviewed scope?`)) await runWbs([{ op: 'cancel_task', task_id: selected.task_id, reason: reason.trim(), successor_refs: successorRefs }]);
      return;
    }
    if (command === 'delete') { if (window.confirm(`Delete ${selected.task_id}?`)) await runWbs([{ op: 'delete_task', task_id: selected.task_id }], [{ op: 'restore_task', task_id: selected.task_id, task: selected }]); return; }
    const siblings = tasks.filter((item) => item.parent_id === selected.parent_id).sort((a, b) => a.order - b.order);
    const index = siblings.findIndex((item) => item.task_id === selected.task_id);
    if ((command === 'up' && index > 0) || (command === 'down' && index < siblings.length - 1)) {
      const { operations: ops, inverse } = moveOperations(tasks, selected.task_id, command);
      await runWbs(ops, inverse); return;
    }
    if (command === 'indent' && index > 0) await runWbs([{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: siblings[index - 1].task_id }], [{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: selected.parent_id }]);
    if (command === 'outdent' && selected.parent_id) {
      const parent = tasks.find((item) => item.task_id === selected.parent_id);
      await runWbs([{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: parent?.parent_id || null }], [{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: selected.parent_id }]);
    }
  }

  async function previewPaste(text) {
    const view = captureView();
    let rows;
    try { rows = parseTsv(text); } catch (error) { ui.announce(error.message); return; }
    if (rows[0]?.[0].toLowerCase() === 'task id' && rows[0]?.[1].toLowerCase() === 'field') rows.shift();
    const allowed = new Set(['parent_id', 'order', 'type', 'title', 'human_owner', 'agent_kind', 'priority', 'target_date', 'estimate', 'description', 'acceptance', 'verification', 'depends_on', 'requirements', 'storyboards', 'design_refs', 'integration_impact', 'risk', 'notes']);
    const taskIds = new Set(state.wbs.tasks.map((item) => item.task_id));
    const seen = new Set();
    for (const row of rows) {
      const key = `${row[0]}:${row[1]}`;
      if (!taskIds.has(row[0])) { ui.announce(`Unknown task ${row[0]}.`); return; }
      if (!allowed.has(row[1])) { ui.announce(`Field ${row[1]} is not editable.`); return; }
      if (seen.has(key)) { ui.announce(`Duplicate TSV field ${key}.`); return; }
      seen.add(key);
      try { parseTsvValue(row[1], row.slice(2).join('\t')); } catch (error) { ui.announce(error.message); return; }
    }
    const operations = rows.map((row) => ({ op: 'set_field', task_id: row[0], field: row[1], value: parseTsvValue(row[1], row.slice(2).join('\t')) }));
    const serverPreview = await api.previewWbs(view.changeId, view.revision, operations);
    if (!view.current()) return;
    const blocking = (serverPreview.findings || []).filter((item) => item.severity === 'blocking');
    const preview = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Paste preview' });
    // One undo step restores every pasted field to its value before Apply.
    const inverse = operations.map((op) => {
      const task = state.wbs.tasks.find((item) => item.task_id === op.task_id);
      return { op: 'set_field', task_id: op.task_id, field: op.field, value: task[API_TO_TASK_FIELD[op.field] || op.field] };
    });
    const apply = button(`Apply ${rows.length} rows`, async () => {
      if (!view.current(false)) return;
      await runWbs(operations, inverse); overlay.close(preview, { force: true });
    }, 'btn btn-primary');
    apply.disabled = blocking.length > 0;
    preview.append(ui.el('h2', null, 'Transactional TSV preview'), ui.el('p', null, 'Columns: Task ID, field, value. Quoted cells and multiline values are preserved.'), simpleTable(['Task', 'Field', 'Value'], rows.map((row) => [row[0], row[1], row.slice(2).join('\t')])), renderFindings(ui, serverPreview.findings), ui.el('div', { class: 'pm-actions' }, [apply, button('Cancel', () => overlay.close(preview))]));
    openDialog(preview, { initialFocus: apply });
  }

  async function copyTsv(tasks) {
    await navigator.clipboard.writeText(serializeWbsTsv(tasks)); ui.announce('Canonical WBS TSV copied.');
  }

  async function exportPlanning(format) { const item = await api.export(state.change.change_id, state.change.revision, format); await api.download(`/exports/${item.export_id}/file`, item.filename); }
  async function importWorkbook(file) { return api.importPreview(state.change.change_id, state.change.revision, file.name, await fileToBase64(file)); }
  async function applyImport(preview, resolutions) { await api.importApply(state.change.change_id, state.change.revision, preview.import_id, preview.preview_digest, resolutions, preview.apply_operation_id); await refreshChange(); ui.announce(pmText('Reviewed workbook changes applied.')); }

  async function previewHandoff() {
    const view = captureView();
    const preview = await api.handoffPreview(view.changeId, view.revision);
    if (!view.current()) return;
    const korean = window.i18n?.lang === 'ko';
    const summary = preview.items.map((item) => `${item.task_id} → ${item.deterministic_ticket_id}${item.runnable ? '' : korean ? ' (차단됨)' : ' (blocked)'}`).join('\n');
    showConfirmation('Confirm native handoff', korean ? `이 승인된 리비전에서 티켓 ${preview.items.length}개를 생성합니다.` : `${preview.items.length} tickets will be created from this exact approved revision.`, summary, 'Create native tickets', async () => {
      await api.handoff(view.changeId, view.revision, preview.preview_token, preview.preview_token); ui.announce(korean ? '승인된 계획을 전달했습니다.' : 'Approved plan handed off.'); await refreshChange();
    });
  }

  async function previewArchive() {
    const view = captureView();
    const preview = await api.archivePreview(view.changeId, view.revision);
    if (!view.current()) return;
    const target = preview.candidate?.target_paths?.[0] || 'archive';
    const accepted = Object.entries(preview.accepted_commits || {}).map(([ticket, commit]) => `${ticket} · ${commit}`).join('\n');
    showConfirmation('Confirm archive', 'All active scope is explicitly accepted. Archiving mutates only the controlled planning worktree.', `${target}\n\nAccepted evidence:\n${accepted}`, 'Archive reviewed revision', async () => {
      await api.archive(view.changeId, view.revision, preview.archive_id, preview.confirmation_token);
      ui.announce('Planning change archived in its controlled worktree.'); await load();
    });
  }

  function showConfirmation(title, description, details, actionLabel, action) {
    const view = captureView();
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': title });
    const close = () => overlay.close(dialog);
    const confirm = button(actionLabel, async () => { if (!view.current(false)) return; confirm.disabled = true; try { await action(); close(); } finally { confirm.disabled = false; } }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, title), ui.el('p', null, description), ui.el('pre', null, details), ui.el('div', { class: 'pm-actions' }, [confirm, button('Cancel', close)]));
    openDialog(dialog, { initialFocus: confirm });
  }

  function showFindings(report) {
    const findings = (report.findings || []).map((finding) => finding.code === 'idea_field_required'
      && finding.field === 'problem' && finding.message === 'Idea field problem is required'
      ? { ...finding, message: pmText('The Problem field is missing. Add it to the idea source, save, then validate again.') }
      : finding);
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Validation results' }, [ui.el('h2', null, report.valid ? 'Ready for review' : 'Needs attention'), renderFindings(ui, findings), button('Close', () => overlay.close(dialog))]);
    openDialog(dialog);
  }

  function showProposal(response) {
    const proposal = response.result || response;
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning proposal' });
    const apply = limitAction(limitAction(button('Apply proposal', async () => {
      await api.applyPlanning(state.change.change_id, state.change.revision, response.operation_id, proposal.proposal_digest);
      overlay.close(dialog, { force: true }); await refreshChange(); ui.announce('Proposal applied as a new draft revision.');
    }, 'btn btn-primary'), 'assistance'), 'authoring');
    const proposals = proposal.proposals || (proposal.proposal_patch ? [{ document_id: proposal.document_id, proposal_patch: proposal.proposal_patch }] : []);
    if (!proposals.length) apply.disabled = true;
    const scope = proposal.scope || {};
    const reserved = Object.entries(proposal.reserved_ids || {}).map(([placeholder, permanentId]) => [placeholder, permanentId]);
    const discard = button('Discard proposal', async () => {
      await api.cancelOperation(response.operation_id);
      overlay.close(dialog, { force: true }); ui.announce('Planning proposal discarded and reserved identities retired.');
    });
    dialog.append(ui.el('h2', null, 'Review proposal'), ui.el('p', null, proposal.explanation || 'This proposal has not changed the plan.'),
      ui.el('dl', { class: 'pm-task-meta' }, [
        ui.el('dt', null, 'Action'), ui.el('dd', null, proposal.action || '—'),
        ui.el('dt', null, 'Target artifact'), ui.el('dd', null, scope.document_id || 'Whole change'),
        ui.el('dt', null, 'Section'), ui.el('dd', null, scope.section_id || '—'),
        ui.el('dt', null, 'Dependency'), ui.el('dd', null, scope.dependency ? `${scope.dependency.task_id} → ${scope.dependency.predecessor_id}` : '—'),
        ui.el('dt', null, 'Impacted artifacts'), ui.el('dd', null, (scope.impacted_document_ids || []).join(', ') || '—'),
        ui.el('dt', null, 'External writes or dispatch'), ui.el('dd', null, proposal.write_or_dispatch_performed ? 'Reported' : 'None performed'),
      ]),
      ...(proposal.blocked_instruction ? [ui.el('p', { class: 'pm-status-card danger', role: 'alert' }, `Blocked instruction: ${proposal.blocked_instruction}`)] : []),
      ...(reserved.length ? [ui.el('section', { class: 'pm-proposal-ids' }, [ui.el('h3', null, 'Reserved permanent identities'), simpleTable(['Placeholder', 'Permanent ID'], reserved)])] : []),
      ...proposals.map((item) => ui.el('section', { class: 'pm-proposal-diff' }, [ui.el('h3', null, item.document_id), ui.el('pre', null, item.proposal_patch || '')])),
      ui.el('div', { class: 'pm-actions' }, [apply, discard]));
    openDialog(dialog, { initialFocus: apply });
  }

  async function openPlanningDialog(docId) {
    const view = captureView();
    const planningWbs = state.wbs || await api.wbs(view.changeId);
    if (!view.current()) return;
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning assistant' });
    const action = ui.el('select', { class: 'input', 'aria-label': 'Planning action' }, PLANNING_ACTIONS.map(([id, label]) => ui.el('option', { value: id, selected: id === 'find_gaps' }, label)));
    const documents = state.change.documents.filter((item) => item.editable);
    const documentSelect = ui.el('select', { class: 'input', 'aria-label': 'Artifact' }, documents.map((item) => ui.el('option', { value: item.document_id, selected: item.document_id === docId }, item.document_id)));
    const section = ui.el('select', { class: 'input', 'aria-label': 'Section' });
    const dependencyOptions = (planningWbs?.tasks || []).flatMap((task) => (task.dependencies || []).map((dependency) => ({ task_id: task.task_id, predecessor_id: dependency.task_id })));
    const label = taskLabeler(planningWbs?.tasks || []);
    const dependency = ui.el('select', { class: 'input', 'aria-label': 'Dependency' }, [ui.el('option', { value: '' }, pmText('Choose dependency')), ...dependencyOptions.map((item) => ui.el('option', { value: `${item.task_id}|${item.predecessor_id}` }, pmMessage('pm.deps.option', { task: label(item.task_id), predecessor: label(item.predecessor_id) })))]);
    const impacted = ui.el('select', { class: 'input', multiple: true, size: Math.min(6, Math.max(2, documents.length)), 'aria-label': 'Impacted artifacts' }, documents.map((item) => ui.el('option', { value: item.document_id, selected: item.document_id === docId }, item.document_id)));
    const instruction = ui.el('textarea', { class: 'input pm-drawer-textarea', 'aria-label': 'Instructions' });
    const sectionField = field('Section', section), dependencyField = field('Dependency', dependency), impactField = field('Impacted artifacts', impacted);
    const updateSections = () => {
      const prior = section.value;
      section.replaceChildren(ui.el('option', { value: '' }, pmText('Choose section')), ...(state.change.planning_sections || []).filter((item) => item.document_id === documentSelect.value).map((item) => ui.el('option', { value: item.section_id }, item.label)));
      if ([...section.options].some((item) => item.value === prior)) section.value = prior;
    };
    const updateScope = () => {
      if (action.value === 'propose_wbs' && documents.some((item) => item.document_id === 'tasks')) documentSelect.value = 'tasks';
      updateSections();
      sectionField.hidden = action.value !== 'refine_section';
      dependencyField.hidden = action.value !== 'explain_dependency';
      impactField.hidden = !['review_consistency', 'update_impacted_artifacts', 'find_gaps'].includes(action.value);
    };
    action.addEventListener('change', updateScope); documentSelect.addEventListener('change', updateSections); updateScope();
    const close = () => overlay.close(dialog);
    const run = button('Prepare proposal', async () => {
      const [taskId, predecessorId] = dependency.value.split('|');
      const request = planningActionRequest(action.value, {
        documentId: documentSelect.value, sectionId: section.value || null,
        dependency: taskId && predecessorId ? { task_id: taskId, predecessor_id: predecessorId } : null,
        impactedDocumentIds: [...impacted.selectedOptions].map((item) => item.value), instruction: instruction.value,
      });
      overlay.close(dialog, { force: true }); await runPlanning(request);
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, 'Planning assistant'),
      field('Action', action), field('Artifact', documentSelect), sectionField, dependencyField, field('Instructions', instruction), impactField,
      ui.el('p', { class: 'pm-empty' }, 'The server reloads the selected revision and enforces allowed targets. Viewing never starts AI.'),
      ui.el('div', { class: 'pm-actions' }, [run, button('Cancel', close)]));
    openDialog(dialog, { initialFocus: action });
  }

  async function runPlanning(request) {
    const running = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning assistance' });
    const task = api.startPlanning(state.change.change_id, state.change.revision, request);
    const cancel = button('Cancel proposal', async () => {
      cancel.disabled = true;
      try {
        await api.cancelOperation(task.operationId);
        overlay.close(running, { force: true }); ui.announce('Planning proposal cancelled.');
      } finally {
        if (running.isConnected) cancel.disabled = false;
      }
    });
    running.append(ui.el('h2', null, 'Preparing a proposal'), ui.el('p', { role: 'status' }, 'The assistant can propose a diff only. Project code and workflow dispatch are unavailable.'), cancel);
    openDialog(running, { initialFocus: cancel, canClose: () => { cancel.click(); return false; } });
    try { const response = await task.promise; if (running.isConnected) { overlay.close(running, { force: true }); showProposal(response); } }
    catch (error) { if (running.isConnected) { overlay.close(running, { force: true }); reportActionError(error, cancel); } }
  }

  async function openCreateDialog() {
    const locator = window.prompt('Change locator (lowercase words with hyphens)'); if (!locator) return;
    const title = window.prompt('Change title', locator.replaceAll('-', ' ')); const workspacePath = window.prompt('Existing isolated Git worktree path'); if (!workspacePath) return;
    await api.create(workspacePath, locator, title || locator); await load();
  }

  async function repairWorkspace() {
    if (!state.change) { ui.announce('Choose a change first.'); return; }
    const path = window.prompt('Attached planning worktree path'); if (!path) return;
    await api.repair(state.change.change_id, path); await refreshChange(); ui.announce('Workspace claim repaired and reindexed.');
  }

  async function convertLegacy() {
    const sourceTicket = window.prompt('Legacy ticket identifier'); if (!sourceTicket) return;
    const workspacePath = window.prompt('Attached planning worktree path'); if (!workspacePath) return;
    const locator = window.prompt('New change locator'); if (!locator) return;
    await api.convertLegacy(sourceTicket, workspacePath, locator, locator.replaceAll('-', ' ')); await load(); ui.announce('Legacy plan converted into a new planning change.');
  }

  function buildWelcome() { return ui.el('section', { class: 'pm-welcome' }, [ui.el('div', { class: 'pm-welcome-mark' }, 'A'), ui.el('h2', null, 'Choose or create a planning change'), ui.el('p', null, 'Planning remains isolated until explicit approval and handoff.')]); }
  function empty(text) { return ui.el('section', { class: 'pm-empty-state' }, [ui.el('p', null, text)]); }
  function field(label, control) { return ui.el('label', { class: 'pm-drawer-field' }, [ui.el('span', null, label), control]); }
  function button(label, handler, className = 'btn') {
    const item = ui.el('button', { class: className, type: 'button' }, label);
    item.addEventListener('click', async (event) => {
      try { await handler(event); } catch (error) { reportActionError(error, item); }
    });
    return item;
  }
  function segmented(items, active, handler) { return ui.el('div', { class: 'pm-segmented', role: 'tablist' }, items.map(([value, label]) => { const item = button(label, () => handler(value), `pm-segment ${active === value ? 'active' : ''}`); item.setAttribute('role', 'tab'); item.setAttribute('aria-selected', String(active === value)); return item; })); }
  // A cell is a plain value, or { text, attrs } when it needs cell attributes.
  function simpleTable(headers, rows) { return ui.el('div', { class: 'pm-table-scroll' }, ui.el('table', { class: 'pm-compact-table' }, [ui.el('thead', null, ui.el('tr', null, headers.map((name) => ui.el('th', { scope: 'col' }, name)))), ui.el('tbody', null, rows.map((row) => ui.el('tr', null, row.map((value) => value !== null && typeof value === 'object' ? ui.el('td', value.attrs || null, String(value.text ?? '—')) : ui.el('td', null, String(value ?? '—'))))))])); }

  compactMedia?.addEventListener?.('change', syncCompact);
  load().catch((error) => { if (error.name !== 'AbortError') root.replaceChildren(ui.el('div', { class: 'banner banner-danger', role: 'alert' }, error.message)); });
  return { unmount: () => { state.dispose(); overlay.destroy(); disposeWbs(); compactMedia?.removeEventListener?.('change', syncCompact); planActions = null; compactMoves = []; root.classList.remove('pm-workspace-host'); }, beforeProjectSwitch: () => guard('Discard unsaved PM Workspace edits and switch projects?'), isDirty: () => state.dirty };
}

const TASK_TO_API_FIELD = { kind: 'type', agent_kind_override: 'agent_kind', dependencies: 'depends_on', requirement_refs: 'requirements', storyboard_refs: 'storyboards' };
const API_TO_TASK_FIELD = Object.fromEntries(Object.entries(TASK_TO_API_FIELD).map(([task, api]) => [api, task]));

function extractSection(source, name) {
  const aliases = { ux: ['ux', 'experience'], storyboard: ['storyboard', 'flow'], architecture: ['architecture'], erd: ['erd', 'data model'], api: ['api'], integration: ['integration'] }[name] || [name];
  const lines = String(source || '').split('\n'); let start = -1, level = 0, end = lines.length;
  for (let i = 0; i < lines.length; i += 1) { const match = lines[i].match(/^(#{1,6})\s+(.+)/); if (match && aliases.some((item) => match[2].toLowerCase().includes(item))) { start = i; level = match[1].length; break; } }
  if (start < 0) return '';
  for (let i = start + 1; i < lines.length; i += 1) { const match = lines[i].match(/^(#{1,6})\s+/); if (match && match[1].length <= level) { end = i; break; } }
  return lines.slice(start, end).join('\n');
}
function diagramBlocks(source) { return [...String(source || '').matchAll(/```(?:mermaid)?\s*\n([\s\S]*?)```/gi)].map((match) => match[1]).filter((item) => /^\s*(flowchart|graph|erDiagram)\b/.test(item)); }
function lineDelta(base, proposed) {
  const left = new Set(String(base || '').split('\n')); const right = new Set(String(proposed || '').split('\n'));
  return { added: [...right].filter((line) => !left.has(line)).length, removed: [...left].filter((line) => !right.has(line)).length };
}
function fileToBase64(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || ''); reader.onerror = () => reject(reader.error); reader.readAsDataURL(file); }); }
function parseTsvValue(field, value) {
  if (field === 'order') { const number = Number(value); if (!Number.isInteger(number)) throw new Error('Order must be an integer.'); return number; }
  if (['acceptance', 'verification', 'depends_on', 'requirements', 'storyboards', 'design_refs'].includes(field)) {
    try { const parsed = JSON.parse(value || '[]'); if (!Array.isArray(parsed)) throw new Error(); return parsed; } catch (_) { throw new Error(`${field} must contain a JSON array.`); }
  }
  return value === '' && ['parent_id', 'human_owner', 'priority', 'estimate', 'target_date'].includes(field) ? null : value;
}

export function moveOperations(tasks, taskId, direction) {
  const selected = tasks.find((item) => item.task_id === taskId);
  if (!selected) throw new Error('Unknown selected task.');
  const siblings = tasks.filter((item) => item.parent_id === selected.parent_id).sort((a, b) => a.order - b.order);
  const index = siblings.findIndex((item) => item.task_id === taskId);
  const other = siblings[index + (direction === 'up' ? -1 : 1)];
  if (!other) throw new Error('Task cannot move farther.');
  return {
    operations: [{ op: 'set_field', task_id: selected.task_id, field: 'order', value: other.order }, { op: 'set_field', task_id: other.task_id, field: 'order', value: selected.order }],
    inverse: [{ op: 'set_field', task_id: selected.task_id, field: 'order', value: selected.order }, { op: 'set_field', task_id: other.task_id, field: 'order', value: other.order }],
  };
}

export function chooseAvailableChange(changes, currentId = '', requestedId = '') {
  return changes.find((item) => item.change_id === currentId)
    || changes.find((item) => item.change_id === requestedId)
    || changes[0]
    || null;
}

export function directHistoryChangeId(changes, requestedId = '') {
  return requestedId && !changes.some((item) => item.change_id === requestedId)
    ? requestedId : '';
}

export function withProjectIdentity(path, projectId) {
  const value = String(path || '');
  if (!projectId || /[?&]project_id=/.test(value)) return value;
  return `${value}${value.includes('?') ? '&' : '?'}project_id=${encodeURIComponent(projectId)}`;
}

export function parseTsv(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted && char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (!quoted && char === '\t') { row.push(cell); cell = ''; }
    else if (!quoted && (char === '\n' || char === '\r')) { if (char === '\r' && text[i + 1] === '\n') i += 1; row.push(cell); if (row.some((value) => value !== '')) rows.push(row); row = []; cell = ''; }
    else cell += char;
  }
  if (quoted) throw new Error('TSV contains an unclosed quoted field.');
  row.push(cell); if (row.some((value) => value !== '')) rows.push(row);
  if (rows.some((item) => item.length < 3)) throw new Error('Every TSV row needs Task ID, field, and value.');
  return rows;
}

export function serializeWbsTsv(tasks) {
  const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  const fields = [['parent_id', 'parent_id'], ['order', 'order'], ['type', 'kind'], ['title', 'title'], ['human_owner', 'human_owner'], ['agent_kind', 'agent_kind_override'], ['priority', 'priority'], ['estimate', 'estimate'], ['target_date', 'target_date'], ['description', 'description'], ['acceptance', 'acceptance'], ['verification', 'verification'], ['depends_on', 'dependencies'], ['requirements', 'requirement_refs'], ['storyboards', 'storyboard_refs'], ['design_refs', 'design_refs'], ['integration_impact', 'integration_impact'], ['risk', 'risk'], ['notes', 'notes']];
  const rows = tasks.flatMap((item) => fields.map(([field, key]) => [item.task_id, field, typeof item[key] === 'object' ? JSON.stringify(item[key]) : item[key] ?? '']));
  return [['Task ID', 'Field', 'Value'], ...rows].map((row) => row.map(quote).join('\t')).join('\n');
}

export function applyUnifiedDiff(source, patch) {
  const input = String(source).split('\n'); const output = []; let cursor = 0; const lines = String(patch || '').split('\n'); let i = 0, sawHunk = false;
  while (i < lines.length) {
    const header = lines[i].match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/); if (!header) { i += 1; continue; }
    sawHunk = true; const start = Number(header[1]) - 1; output.push(...input.slice(cursor, start)); cursor = start; i += 1;
    while (i < lines.length && !lines[i].startsWith('@@ ')) {
      const line = lines[i]; if (line.startsWith('\\ No newline')) { i += 1; continue; }
      const mark = line[0], value = line.slice(1);
      if (mark === ' ' || mark === '-') { if (input[cursor] !== value) throw new Error('Proposal no longer matches this revision.'); if (mark === ' ') output.push(value); cursor += 1; }
      else if (mark === '+') output.push(value); else if (!line.startsWith('---') && !line.startsWith('+++')) break;
      i += 1;
    }
  }
  if (!sawHunk) throw new Error('Proposal does not contain an applicable unified diff.');
  output.push(...input.slice(cursor)); return output.join('\n');
}

const KO = new Map(Object.entries({
  'Discard unsaved changes?': '저장하지 않은 변경을 버리시겠습니까?',
  'Discard unsaved changes and leave this tab?': '저장하지 않은 변경을 버리고 이 탭을 나가시겠습니까?',
  'Discard unsaved PM edits and switch changes?': '저장하지 않은 기획 변경을 버리고 다른 변경으로 이동하시겠습니까?',
  'Discard unsaved PM Workspace edits and switch projects?': '저장하지 않은 기획 변경을 버리고 프로젝트를 바꾸시겠습니까?',
  'Discard unsaved storyboard changes?': '저장하지 않은 스토리보드 변경을 버리시겠습니까?',
  'Recovered local draft': '복원된 로컬 초안',
  'Action unavailable': '작업을 수행할 수 없습니다',
  'Title': '제목', 'Screen / route': '화면 / 경로', 'Actor and goal': '사용자와 목표', 'Initial state': '초기 상태',
  'User action': '사용자 행동', 'System response': '시스템 반응', 'Success': '성공', 'Alternate': '다른 경로', 'Failure': '실패',
  'Product planning': '제품 기획', 'Artifacts': '기획 산출물',
  'Shape the idea, align requirements, and hand off executable work.': '아이디어를 구체화하고 요구사항을 맞춘 뒤 실행 가능한 작업으로 전달합니다.',
  'Idea': '아이디어', 'Requirements': '요구사항', 'Design': '설계', 'Plan': '계획', 'Code Files': '코드 파일',
  'New change': '새 변경', 'Repair workspace': '작업공간 복구', 'Convert legacy plan': '기존 계획 변환',
  'Add capability': '기능 요구사항 추가', 'Back to requirements': '요구사항으로 돌아가기', 'Edit requirements': '요구사항 편집',
  'Add decision': '결정 추가', 'Add storyboard': '스토리보드 추가', 'Add contract': '계약 추가', 'Add requirement': '요구사항 추가', 'Add scenario': '시나리오 추가',
  'Edit in external editor': '외부 편집기로 열기', 'Finish external edit & reindex': '외부 편집 종료 및 재색인',
  'Validate': '검증', 'Approve revision': '리비전 승인', 'Edit source': '소스 편집', 'Review document': '문서 검토',
  'Readable view': '읽기 보기', 'Source': '소스', 'Save draft': '초안 저장', 'Find gaps': '누락 찾기', 'Planning assistant': '기획 도우미',
  'Draft artifact': '산출물 초안', 'Refine selected section': '선택 구역 다듬기', 'Review consistency': '일관성 검토', 'Explain dependency': '의존성 설명', 'Propose WBS': 'WBS 제안', 'Update impacted artifacts': '영향받는 산출물 업데이트',
  'Action': '작업', 'Planning action': '기획 작업', 'Artifact': '산출물', 'Section': '구역', 'Choose section': '구역 선택', 'Dependency': '의존성', 'Choose dependency': '의존성 선택',
  'Instructions': '지시사항', 'Impacted artifacts': '영향받는 산출물', 'Prepare proposal': '제안 준비', 'Cancel proposal': '제안 취소', 'Cancel': '취소',
  'Preparing a proposal': '제안 준비 중', 'Review proposal': '제안 검토', 'Apply proposal': '제안 적용', 'Discard proposal': '제안 폐기',
  'UX': '사용자 경험', 'Storyboard': '스토리보드', 'Architecture': '아키텍처', 'ERD': 'ERD', 'API': 'API', 'Integration': '통합',
  'Work breakdown': '작업 분해', 'Dependencies': '의존성', 'Plan document': '계획 문서',
  'Preview archive': '아카이브 미리보기', 'Preview approved handoff': '승인된 전달 미리보기',
  'Archive requires an approved revision. Review and approve this revision, then preview archive again.': '승인된 리비전이 있어야 아카이브할 수 있습니다. 현재 리비전을 검토하고 승인한 뒤 아카이브 미리보기를 다시 여세요.',
  'The Problem field is missing. Add it to the idea source, save, then validate again.': '문제 항목이 빠져 있습니다. 아이디어 소스에 문제 항목을 추가하고 저장한 뒤 다시 검증하세요.',
  'Validation results': '검증 결과', 'Ready for review': '검토 준비 완료', 'Needs attention': '조치 필요', 'No findings.': '발견된 문제가 없습니다.', 'Close': '닫기',
  'Confirm native handoff': '네이티브 전달 확인', 'Create native tickets': '네이티브 티켓 생성',
  'Add group': '그룹 추가', 'Add root': '최상위 추가', 'Add sibling': '같은 단계 추가', 'Add child': '하위 추가', 'Indent': '들여쓰기', 'Outdent': '내어쓰기',
  'Move up': '위로', 'Move down': '아래로', 'Delete': '삭제', 'Undo': '실행 취소', 'Redo': '다시 실행', 'Copy TSV': 'TSV 복사',
  'Workbook exchange': '워크북 교환', 'Download export': '내보내기 다운로드', 'Preview import': '가져오기 미리보기',
  'Export format': '내보내기 형식', 'Excel workbook': 'Excel 워크북', 'Print HTML': '인쇄용 HTML', 'Planning package': '기획 패키지', 'Choose XLSX workbook': 'XLSX 워크북 선택',
  'Export a revision-bound workbook or preview a three-way import. Nothing is applied from preview.': '리비전에 연결된 워크북을 내보내거나 3방향 가져오기를 미리 봅니다. 미리보기만으로는 변경이 적용되지 않습니다.',
  'Apply reviewed import': '검토한 가져오기 적용', '{count} proposed changes': '제안된 변경 {count}개', '{count} conflicts require resolution': '해결이 필요한 충돌 {count}개',
  'Item': '항목', 'Field': '필드', 'Base': '기준', 'Current': '현재', 'Workbook': '워크북', 'Resolution': '해결',
  'Resolve {key}': '{key} 해결', 'Choose…': '선택…', 'Keep current': '현재 값 유지', 'Use workbook': '워크북 사용',
  'Choose a workbook first.': '먼저 워크북을 선택하세요.', 'Choose an XLSX workbook with a plain filename.': '경로가 없는 일반 파일명의 XLSX 워크북을 선택하세요.', 'Only XLSX workbooks are supported.': 'XLSX 워크북만 지원됩니다.',
  'Reviewed workbook changes applied.': '검토한 워크북 변경을 적용했습니다.', 'Approve this exact planning revision locally?': '이 정확한 기획 리비전을 로컬에서 승인하시겠습니까?', 'Revision approved.': '리비전을 승인했습니다.',
  'Type': '유형', 'Human owner': '담당자', 'Agent override': '에이전트 지정', 'Priority': '우선순위', 'Target date': '목표 날짜', 'Estimate': '예상치',
  'Description': '설명', 'Acceptance': '인수 조건', 'Verification': '검증', 'Dependencies': '의존성', 'Requirement references': '요구사항 참조', 'Storyboard references': '스토리보드 참조', 'Design references': '설계 참조', 'Integration impact': '통합 영향', 'Risk': '위험', 'Notes': '메모',
  'Planning files': '기획 파일', 'Handoff tickets': '전달 티켓', 'Review verification': '검증 검토', 'Accepted': '승인됨', 'Run verification': '검증 실행', 'Submit completion evidence': '완료 증거 제출', 'Host executed': '호스트 실행', 'Manual reported': '수동 보고', 'Mixed': '혼합',
  'Tested commit': '검증 커밋', 'Environment summary': '환경 요약', 'Manual result': '수동 결과', 'Started at': '시작 시각', 'Completed at': '완료 시각', 'Report path': '보고서 경로', 'Artifact references': '증거 자료 참조',
  'Preview host verification': '호스트 검증 미리보기', 'Preview evidence': '증거 미리보기', 'Run approved command': '승인된 명령 실행', 'Submit evidence': '증거 제출', 'Acceptance review': '인수 검토', 'Accept verified revision': '검증된 리비전 인수',
  'Requirement and scenario coverage': '요구사항 및 시나리오 커버리지', 'Waive coverage': '커버리지 면제', 'Revoke waiver': '면제 취소', 'Preview waiver': '면제 미리보기',
  'Waiver reason': '면제 사유', 'Reviewer label': '검토자 이름', 'Confirm waiver': '면제 확인',
  'Proposed': '변경안', 'Current baseline': '현재 기준', 'Source revision': '소스 리비전',
  'External editing is active': '외부 편집 중', 'Choose or create a planning change': '기획 변경을 선택하거나 만드세요',
  'Save storyboard': '스토리보드 저장', 'Cancel task': '작업 취소', 'Review Git commit': 'Git 커밋 검토', 'Open run evidence': '실행 증거 열기',
  'Demo workspace': '데모 작업공간',
  'This is a disposable demo. Planning actions are unlocked only on this server; no AI worker starts and nothing leaves this computer.': '일회용 데모입니다. 기획 작업은 이 서버에서만 열려 있으며, AI 작업자는 시작되지 않고 어떤 내용도 이 컴퓨터 밖으로 나가지 않습니다.',
  'Off in the demo: it would contact an AI model.': '데모에서는 꺼져 있습니다. AI 모델에 연결해야 하기 때문입니다.',
  // Button guides (guide.js).
  'What do these buttons do?': '이 버튼들은 무엇을 하나요?',
  'Creates a new planning change in a separate Git working folder (worktree) that you name. Other changes stay as they are.': '지정한 별도 Git 작업 폴더(worktree)에 새 기획 변경을 만듭니다. 다른 변경은 그대로 둡니다.',
  'Reconnects this change to its planning folder after the folder was moved or lost, then reads its documents again.': '기획 폴더가 옮겨졌거나 사라졌을 때 이 변경을 폴더에 다시 연결하고 문서를 다시 읽습니다.',
  'Replaces an older board ticket with a new planning change: the ticket is parked, and only tasks approved here can carry out its work.': '기존 보드 티켓을 새 기획 변경으로 대체합니다. 티켓은 보류되고, 여기서 승인된 작업만 그 일을 수행할 수 있습니다.',
  'Pauses editing on this page so you can change the planning files in another editor; finishing reads and validates them again.': '다른 편집기에서 기획 파일을 고칠 수 있도록 이 화면의 편집을 멈춥니다. 편집을 마치면 파일을 다시 읽고 검증합니다.',
  'Checks every planning document against the rules and OpenSpec and lists what blocks approval. Changes nothing.': '모든 기획 문서를 규칙과 OpenSpec으로 점검하고 승인을 막는 항목을 보여 줍니다. 아무것도 바꾸지 않습니다.',
  'Runs the same checks as Validate and records your sign-off on this exact version of the plan. Any later edit needs a new approval before handoff.': '검증과 같은 점검을 거친 뒤 이 정확한 버전의 계획에 대한 승인을 기록합니다. 이후에 수정하면 전달 전에 다시 승인해야 합니다.',
  'Opens the plain text of this document for editing; nothing is saved until you press Save draft.': '이 문서의 원문을 편집할 수 있게 엽니다. 초안 저장을 누르기 전에는 저장되지 않습니다.',
  'Saves your edits as a new revision of the plan. It does not approve or hand off anything.': '편집한 내용을 계획의 새 리비전으로 저장합니다. 승인이나 전달은 하지 않습니다.',
  'Asks an AI assistant to suggest changes to the plan; you review the proposal, and nothing changes unless you apply it.': 'AI 도우미에게 계획 변경 제안을 요청합니다. 제안을 검토해 적용하기 전에는 아무것도 바뀌지 않습니다.',
  'Adds a decision with the title you type, with room for its owner, impact, status and reason.': '입력한 제목으로 결정 항목을 추가하고 담당자, 영향, 상태, 사유를 적을 자리를 만듭니다.',
  'Starts a requirements document for a new capability (an area of the product) with a first requirement that you name.': '새 기능(제품의 한 영역)의 요구사항 문서를 만들고, 이름을 지정한 첫 요구사항을 넣습니다.',
  'Opens the requirements of this capability for editing; nothing is saved until you press Save draft.': '이 기능의 요구사항을 편집할 수 있게 엽니다. 초안 저장을 누르기 전에는 저장되지 않습니다.',
  'Adds a requirement with the title you type; each requirement states something the product must do.': '입력한 제목으로 요구사항을 추가합니다. 요구사항은 제품이 반드시 해야 할 일을 적은 것입니다.',
  'Adds a scenario with the title you type: a concrete example that shows a requirement working.': '입력한 제목으로 시나리오를 추가합니다. 시나리오는 요구사항이 실제로 동작하는 모습을 보여 주는 구체적인 예입니다.',
  'Records a reviewed reason why a requirement does not need full coverage. It does not mark any work as done or accepted.': '요구사항에 완전한 커버리지가 필요 없는 이유를 검토 기록으로 남깁니다. 어떤 작업도 완료나 인수로 표시하지 않습니다.',
  'Withdraws an active waiver, so the requirement needs normal coverage again.': '적용 중인 면제를 철회해 요구사항이 다시 일반 커버리지를 갖추어야 하게 합니다.',
  'Shows each screen as a frame: what the user sees and does, and how the system responds.': '화면마다 사용자가 보고 하는 일과 시스템의 반응을 프레임으로 보여 줍니다.',
  'Saves your edits to the open storyboard frame as a new revision of the plan.': '열려 있는 스토리보드 프레임의 수정 내용을 계획의 새 리비전으로 저장합니다.',
  'Adds a storyboard frame with the title you type to the UX document.': '입력한 제목으로 UX 문서에 스토리보드 프레임을 추가합니다.',
  'Adds an API contract with the title you type, describing who owns it, what data it exchanges and how errors are handled.': '입력한 제목으로 API 계약을 추가합니다. 담당자, 주고받는 데이터, 오류 처리 방식을 적습니다.',
  'Shows the plan as a tree of groups and tasks with owner, priority and estimate.': '계획을 그룹과 작업의 트리로 보여 주고 담당자, 우선순위, 예상치를 함께 표시합니다.',
  'Lists which tasks must wait for other tasks, and why.': '어떤 작업이 다른 작업을 기다려야 하는지와 그 이유를 보여 줍니다.',
  'Adds a group that holds related tasks; a group itself is never handed off as work.': '관련 작업을 묶는 그룹을 추가합니다. 그룹 자체는 작업으로 전달되지 않습니다.',
  'Adds a task under the selected group; with no selection, adds a root task. Executable tasks cannot contain children.': '선택한 그룹 아래에 새 작업을 추가합니다. 아무것도 선택하지 않으면 최상위 작업을 추가합니다. 실행 작업 아래에는 하위 작업을 추가할 수 없습니다.',
  'Removes the selected task while the plan is not yet approved; Undo brings it back.': '계획이 승인되기 전에 선택한 작업을 삭제합니다. 실행 취소로 되돌릴 수 있습니다.',
  'Takes the place of Delete after approval: cancels the selected task with a reason you give and keeps its reviewed scope on record.': '승인 후에는 삭제 대신 나타납니다. 입력한 사유로 선택한 작업을 취소하고 검토된 범위는 기록으로 남깁니다.',
  'Reverses your last change to the work breakdown.': '작업 분해에서 마지막으로 바꾼 내용을 되돌립니다.',
  'Downloads the plan as an Excel workbook, CSV, Markdown, printable HTML or a planning package. Changes nothing.': '계획을 Excel 워크북, CSV, Markdown, 인쇄용 HTML 또는 기획 패키지로 내려받습니다. 아무것도 바꾸지 않습니다.',
  'Reads an edited Excel workbook and lists each proposed change and conflict; nothing is applied until you review and apply it.': '수정한 Excel 워크북을 읽어 제안된 변경과 충돌을 하나씩 보여 줍니다. 검토 후 적용하기 전에는 아무것도 반영되지 않습니다.',
  'Shows the board tickets the approved plan would create; nothing is created until you confirm.': '승인된 계획으로 만들어질 보드 티켓을 보여 줍니다. 확인하기 전에는 아무것도 만들지 않습니다.',
  'Checks that all work is accepted and shows what archiving would record; nothing is archived until you confirm.': '모든 작업이 인수되었는지 확인하고 아카이브에 기록될 내용을 보여 줍니다. 확인하기 전에는 아카이브하지 않습니다.',
  'Has this computer run an approved check on the code of a task and records the actual result.': '이 컴퓨터에서 작업의 코드에 승인된 점검을 실행하고 실제 결과를 기록합니다.',
  'Records the result of a check that a person ran, with its report. It does not complete or accept the task.': '사람이 직접 수행한 점검 결과를 보고서와 함께 기록합니다. 작업을 완료하거나 인수하지는 않습니다.',
  'Opens the code change (Git commit) that this task produced.': '이 작업이 만든 코드 변경(Git 커밋)을 엽니다.',
  'Opens the record of the agent run that worked on this task.': '이 작업을 수행한 에이전트 실행 기록을 엽니다.',
  'Signs off the work of a task once all its required checks have passed; nothing is accepted automatically.': '필요한 점검을 모두 통과한 작업을 인수합니다. 자동으로 인수되는 것은 없습니다.',
}));

export function pmText(value, values = {}, language = globalThis.window?.i18n?.lang || 'en') {
  let result = language === 'ko' ? (KO.get(value) || value) : value;
  for (const [key, replacement] of Object.entries(values)) result = result.replaceAll(`{${key}}`, String(replacement));
  return result;
}

// UI-owned dependency text, applied only where it is created. Kept out of KO so
// localizePm never rewrites user content that happens to equal 'Task' or 'Reason'.
const PM_MESSAGES = {
  'pm.deps.col.task': { en: 'Task', ko: '작업' },
  'pm.deps.col.needs': { en: 'Needs', ko: '선행 작업' },
  'pm.deps.col.kind': { en: 'Dependency', ko: '의존 유형' },
  'pm.deps.col.reason': { en: 'Reason', ko: '사유' },
  'pm.deps.kind.requires_code': { en: 'Requires code', ko: '코드 완료 필요' },
  'pm.deps.kind.order_only': { en: 'Order only', ko: '순서만 지정' },
  'pm.deps.option': { en: '{task} needs {predecessor}', ko: '{task} — 선행 작업: {predecessor}' },
  'pm.header.change': { en: 'Planning change', ko: '기획 변경' },
  'pm.header.selectChange': { en: 'Select a change', ko: '변경 선택' },
  'pm.tabs.label': { en: 'Planning artifacts', ko: '기획 산출물 단계' },
  'pm.files.col.path': { en: 'Path', ko: '경로' },
  'pm.files.col.state': { en: 'State', ko: '상태' },
  'pm.files.col.role': { en: 'Role', ko: '역할' },
  'pm.files.role.source': { en: 'Planning source', ko: '기획 원본' },
  'pm.files.role.readOnly': { en: 'Read only', ko: '읽기 전용' },
  'pm.files.state.draft': { en: 'draft', ko: '초안' },
  'pm.state.draft': { en: 'Draft', ko: '초안' },
  'pm.state.validated': { en: 'Validated', ko: '검증됨' },
  'pm.state.needsreview': { en: 'NeedsReview', ko: '검토 필요' },
  'pm.state.approved': { en: 'Approved', ko: '승인됨' },
  'pm.state.stale': { en: 'Stale', ko: '오래됨' },
  'pm.state.archived': { en: 'Archived', ko: '보관됨' },
  'pm.capability.authoring': { en: 'authoring', ko: '작성' },
  'pm.capability.exchange': { en: 'exchange', ko: '교환' },
  'pm.capability.assistance': { en: 'assistance', ko: '기획 지원' },
  'pm.capability.handoff': { en: 'handoff', ko: '인계' },
  'pm.more.change': { en: 'Changes', ko: '변경' },
  'pm.more.revision': { en: 'Revision actions', ko: '리비전 작업' },
  'pm.more.planActions': { en: 'Plan actions', ko: '계획 작업' },
  'pm.more.design': { en: '{view} view · switch view & edit source', ko: '{view} 보기 · 보기 전환 및 소스 편집' },
  'pm.design.view': { en: '{view} view', ko: '{view} 보기' },
  'pm.status.demoSummary': { en: 'Disposable · no AI worker · stays local', ko: '일회용 · AI 작업자 없음 · 로컬 전용' },
  'pm.status.more': { en: 'Details', ko: '자세히' },
  'pm.code.noTickets': { en: 'No managed tickets have been handed off.', ko: '아직 전달된 관리 티켓이 없습니다.' },
  'pm.coverage.independent': { en: 'Specified, linked, implemented, tested, accepted, and waived remain independent.', ko: '명세, 연결, 구현, 테스트, 인수, 면제는 각각 별도로 판단합니다.' },
  'pm.requirements.newBaseline': { en: 'Current baseline · new capability', ko: '현재 기준 · 새 기능' },
  'pm.requirements.noBaseline': { en: 'No baseline specification exists yet.', ko: '아직 기준 명세가 없습니다.' },
  'pm.requirements.meta': { en: '{capability} · path, changes & edit', ko: '{capability} · 경로, 변경 내역 및 편집' },
};

export function pmMessage(key, values = {}, language = globalThis.window?.i18n?.lang || 'en') {
  const entry = Object.hasOwn(PM_MESSAGES, key) ? PM_MESSAGES[key] : null;
  const template = entry ? (entry[language] || entry.en) : key;
  // Single pass: a user title containing '{predecessor}' is never re-interpolated.
  return template.replace(/\{(\w+)\}/g, (match, name) => (Object.hasOwn(values, name) ? String(values[name]) : match));
}

function taskLabeler(tasks) {
  const titles = new Map(tasks.map((task) => [task.task_id, task.title]));
  return (id) => (titles.get(id) ? `${id} · ${titles.get(id)}` : id);
}

// Known kinds get a label; unknown kinds stay visible as-is. The raw kind is kept on the cell.
function dependencyKindCell(kind) {
  const key = `pm.deps.kind.${kind}`;
  return { text: Object.hasOwn(PM_MESSAGES, key) ? pmMessage(key) : kind, attrs: { 'data-kind': kind, title: kind } };
}

// Only the six exact PlanningState values get Korean; anything else stays verbatim.
const PLANNING_STATES = new Set(['Draft', 'Validated', 'NeedsReview', 'Approved', 'Stale', 'Archived']);
function planningStateText(value) {
  return globalThis.window?.i18n?.lang === 'ko' && PLANNING_STATES.has(value) ? pmMessage(`pm.state.${value.toLowerCase()}`) : value;
}

// The planning-files producer emits lowercase draft; other document states stay raw on the cell.
function documentStateCell(value) {
  return { text: globalThis.window?.i18n?.lang === 'ko' && value === 'draft' ? pmMessage('pm.files.state.draft') : value, attrs: { 'data-state': value, title: value } };
}

function capabilityText(name) {
  const key = `pm.capability.${name}`;
  return Object.hasOwn(PM_MESSAGES, key) ? pmMessage(key) : name;
}

function localizePm(scope) {
  if (window.i18n?.lang !== 'ko') return;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const raw = node.nodeValue; const trimmed = raw.trim(); const replacement = pmText(trimmed);
    if (replacement !== trimmed) node.nodeValue = raw.replace(trimmed, replacement);
  }
  const labelled = [...scope.querySelectorAll('[aria-label]')];
  if (scope.matches?.('[aria-label]')) labelled.unshift(scope);
  for (const item of labelled) {
    const current = item.getAttribute('aria-label'); const replacement = pmText(current); if (replacement !== current) item.setAttribute('aria-label', replacement);
  }
}
