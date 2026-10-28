const VALID_TABS = new Set(['idea', 'requirements', 'design', 'plan', 'code']);
const DESIGN_VIEWS = ['ux', 'storyboard', 'architecture', 'erd', 'api', 'integration'];
const PLAN_VIEWS = ['wbs', 'dependencies', 'source'];

export function createPmState(onDirtyChange, projectId = '') {
  const params = new URL(location.href).searchParams;
  const savedTab = params.get('pmTab');
  const requestedView = params.get('pmView');
  const state = {
    capabilities: null,
    changes: [],
    change: null,
    projectId,
    tab: VALID_TABS.has(savedTab) ? savedTab : 'idea',
    requestedChangeId: params.get('pmChange') || '',
    designView: DESIGN_VIEWS.includes(requestedView) ? requestedView : 'ux',
    planView: PLAN_VIEWS.includes(requestedView) ? requestedView : 'wbs',
    planFilter: '', selectedTask: '', planScroll: 0,
    documents: new Map(), baselines: new Map(),
    wbs: null, tickets: [], coverage: null,
    errors: new Map(), dirty: false, externalEdit: false,
    disposed: false,
    epoch: 0, controller: null,
  };
  const scopedKey = (name) => `symphony.pm.${projectId}.${state.change?.change_id || 'none'}.${name}`;
  state.beginLoad = () => {
    state.controller?.abort(); state.controller = new AbortController(); state.epoch += 1;
    return { epoch: state.epoch, signal: state.controller.signal };
  };
  state.isCurrent = (epoch) => !state.disposed && epoch === state.epoch;
  state.setDirty = (dirty) => {
    state.dirty = Boolean(dirty);
    onDirtyChange(state.dirty);
  };
  const updateUrl = () => {
    const url = new URL(location.href);
    url.searchParams.set('pmTab', state.tab);
    if (state.change) url.searchParams.set('pmChange', state.change.change_id);
    else url.searchParams.delete('pmChange');
    const view = state.tab === 'design' ? state.designView : state.tab === 'plan' ? state.planView : '';
    if (view) url.searchParams.set('pmView', view); else url.searchParams.delete('pmView');
    history.replaceState(null, '', url);
  };
  state.updateUrl = updateUrl;
  state.setTab = (tab) => {
    if (!VALID_TABS.has(tab)) return;
    state.tab = tab;
    sessionStorage.setItem(scopedKey('tab'), tab);
    updateUrl();
  };
  // A subview named in the opening URL is an explicit deep link: it wins over
  // the remembered subview for the change it names, once.
  let linkedView = requestedView;
  const linkedChange = params.get('pmChange') || '';
  state.restoreViewState = () => {
    const savedScopedTab = sessionStorage.getItem(scopedKey('tab'));
    if (!params.get('pmTab') && VALID_TABS.has(savedScopedTab)) state.tab = savedScopedTab;
    const linked = linkedView && (!linkedChange || state.change?.change_id === linkedChange);
    if (!(linked && DESIGN_VIEWS.includes(linkedView))) state.designView = sessionStorage.getItem(scopedKey('design')) || state.designView;
    if (!(linked && PLAN_VIEWS.includes(linkedView))) state.planView = sessionStorage.getItem(scopedKey('plan')) || state.planView;
    if (linked) {
      if (DESIGN_VIEWS.includes(linkedView)) sessionStorage.setItem(scopedKey('design'), linkedView);
      if (PLAN_VIEWS.includes(linkedView)) sessionStorage.setItem(scopedKey('plan'), linkedView);
      linkedView = '';
    }
    state.planFilter = sessionStorage.getItem(scopedKey('planFilter')) || '';
    state.selectedTask = sessionStorage.getItem(scopedKey('selectedTask')) || '';
    state.planScroll = Number(sessionStorage.getItem(scopedKey('planScroll')) || 0);
  };
  state.setDesignView = (view) => { state.designView = view; sessionStorage.setItem(scopedKey('design'), view); updateUrl(); };
  state.setPlanView = (view) => { state.planView = view; sessionStorage.setItem(scopedKey('plan'), view); updateUrl(); };
  state.setPlanFilter = (value) => { state.planFilter = value; sessionStorage.setItem(scopedKey('planFilter'), value); };
  state.setSelectedTask = (value) => { state.selectedTask = value || ''; sessionStorage.setItem(scopedKey('selectedTask'), state.selectedTask); };
  state.setPlanScroll = (value) => { state.planScroll = value; sessionStorage.setItem(scopedKey('planScroll'), String(value)); };
  state.draftKey = (doc) => `symphony.pm.draft.${projectId}.${state.change?.change_id || 'none'}.${doc}.${state.change?.revision || 'none'}`;
  state.readDraft = (doc) => { try { return localStorage.getItem(state.draftKey(doc)); } catch (_) { return null; } };
  state.writeDraft = (doc, source) => { try { localStorage.setItem(state.draftKey(doc), source); } catch (_) {} };
  state.clearDraft = (doc) => { try { localStorage.removeItem(state.draftKey(doc)); } catch (_) {} };
  state.dispose = () => { state.disposed = true; state.controller?.abort(); onDirtyChange(false); };
  return state;
}
