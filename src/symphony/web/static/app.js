/*
 * oh-my-symphony board — vanilla SPA (no build step, no framework).
 * Sections: api / state / dom helpers / markdown / utils / toast /
 * overlays (modal, drawer, popover) / shared form fields / router /
 * pages (board, stats, workflow, settings) / poll loop / bootstrap.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // API layer
  // ------------------------------------------------------------------

  const API_BASE = '/api/v1';

  class ApiError extends Error {
    constructor(message, code, status) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  async function apiRequest(path, { method = 'GET', body } = {}) {
    const init = { method };
    if (body !== undefined) {
      init.body = body;
      init.headers = { 'Content-Type': 'application/json' };
    }
    const res = await fetch(API_BASE + path, init);
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_err) {
        data = null;
      }
    }
    if (!res.ok) {
      const err = data && data.error;
      throw new ApiError(
        (err && err.message) || `request failed (${res.status})`,
        (err && err.code) || 'unknown_error',
        res.status
      );
    }
    return data;
  }

  const api = {
    getBoard: () => apiRequest('/board'),
    createIssue: (payload) => apiRequest('/issues', { method: 'POST', body: JSON.stringify(payload) }),
    getIssue: (id) => apiRequest(`/issues/${encodeURIComponent(id)}`),
    patchIssue: (id, fields) => apiRequest(`/issues/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(fields) }),
    deleteIssue: (id) => apiRequest(`/issues/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    getWorkflow: () => apiRequest('/workflow'),
    putWorkflowStates: (states) => apiRequest('/workflow/states', { method: 'PUT', body: JSON.stringify({ states }) }),
    getPrompt: (stateName) => apiRequest(`/workflow/prompts/${encodeURIComponent(stateName)}`),
    putPrompt: (stateName, content) => apiRequest(`/workflow/prompts/${encodeURIComponent(stateName)}`, { method: 'PUT', body: JSON.stringify({ content }) }),
    putBranchPolicy: (payload) => apiRequest('/workflow/branch-policy', { method: 'PUT', body: JSON.stringify(payload) }),
    getBranches: () => apiRequest('/git/branches'),
    getStats: (days) => apiRequest(`/stats?days=${encodeURIComponent(days)}`),
    pause: (id) => apiRequest(`/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
    resume: (id) => apiRequest(`/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
    skipLearn: (id) => apiRequest(`/${encodeURIComponent(id)}/skip-learn`, { method: 'POST' }),
    refresh: () => apiRequest('/refresh', { method: 'POST' }),
  };

  // ------------------------------------------------------------------
  // State store
  // ------------------------------------------------------------------

  const ROUTES = ['board', 'stats', 'workflow', 'settings'];

  const PRIORITY_META = {
    0: { label: 'Urgent', short: 'P0', className: 'p0' },
    1: { label: 'High', short: 'P1', className: 'p1' },
    2: { label: 'Medium', short: 'P2', className: 'p2' },
    3: { label: 'Low', short: 'P3', className: 'p3' },
    4: { label: 'Minor', short: 'P4', className: 'p4' },
  };

  const state = {
    route: 'board',
    board: null,
    workflow: null,
    branches: [],
    connected: false,
    search: '',
    boardScope: 'active',
    mobileColumnIndex: 0,
    statsDays: 30,
    drawerIssue: null,
    workflowDraft: null,
    openModalBackdrop: null,
    openMenu: null,
    wfRerender: null,
  };

  // ------------------------------------------------------------------
  // DOM helpers
  // ------------------------------------------------------------------

  const STRING_BOOLEAN_ATTRS = new Set(['draggable', 'contenteditable', 'spellcheck']);

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null) continue;
      if (key === 'class') {
        node.className = value;
      } else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (STRING_BOOLEAN_ATTRS.has(key)) {
        node.setAttribute(key, value ? 'true' : 'false');
      } else if (typeof value === 'boolean') {
        if (value) node.setAttribute(key, '');
      } else {
        node.setAttribute(key, value);
      }
    }
    const kids = Array.isArray(children) ? children : children != null ? [children] : [];
    for (const kid of kids) {
      if (kid == null || kid === false) continue;
      node.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
    }
    return node;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs, children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    for (const child of children || []) node.appendChild(child);
    return node;
  }

  // ------------------------------------------------------------------
  // Minimal markdown renderer — pure DOM construction, never innerHTML.
  // ------------------------------------------------------------------

  function renderMarkdown(source) {
    const root = document.createDocumentFragment();
    const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
    let i = 0;
    let listBuffer = null;

    function flushList() {
      if (!listBuffer) return;
      const tag = listBuffer.type === 'ol' ? 'ol' : 'ul';
      root.appendChild(el(tag, { class: 'md-list' }, listBuffer.items.map((item) => el('li', null, renderInline(item)))));
      listBuffer = null;
    }

    while (i < lines.length) {
      const line = lines[i];

      const fence = line.match(/^```(\w*)\s*$/);
      if (fence) {
        flushList();
        const codeLines = [];
        i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) {
          codeLines.push(lines[i]);
          i++;
        }
        i++;
        root.appendChild(el('pre', { class: 'md-code-block' }, el('code', null, codeLines.join('\n'))));
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushList();
        root.appendChild(el(`h${heading[1].length}`, { class: 'md-heading' }, renderInline(heading[2])));
        i++;
        continue;
      }

      const ulItem = line.match(/^\s*[-*]\s+(.*)$/);
      if (ulItem) {
        if (!listBuffer || listBuffer.type !== 'ul') {
          flushList();
          listBuffer = { type: 'ul', items: [] };
        }
        listBuffer.items.push(ulItem[1]);
        i++;
        continue;
      }

      const olItem = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (olItem) {
        if (!listBuffer || listBuffer.type !== 'ol') {
          flushList();
          listBuffer = { type: 'ol', items: [] };
        }
        listBuffer.items.push(olItem[1]);
        i++;
        continue;
      }

      flushList();

      if (!line.trim()) {
        i++;
        continue;
      }

      const paraLines = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,6})\s|^```|^\s*[-*]\s|^\s*\d+[.)]\s/.test(lines[i])) {
        paraLines.push(lines[i]);
        i++;
      }
      root.appendChild(el('p', { class: 'md-paragraph' }, renderInline(paraLines.join(' '))));
    }
    flushList();
    return root;
  }

  function renderInline(text) {
    const nodes = [];
    const pattern = /(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))|(\*\*[^*]+\*\*)|(__[^_]+__)|(`[^`]+`)|(\*[^*]+\*)|(_[^_]+_)/g;
    let lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > lastIndex) nodes.push(document.createTextNode(text.slice(lastIndex, match.index)));
      const token = match[0];
      if (token.startsWith('[')) {
        const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
        nodes.push(el('a', { href: linkMatch[2], target: '_blank', rel: 'noopener noreferrer' }, linkMatch[1]));
      } else if (token.startsWith('**') || token.startsWith('__')) {
        nodes.push(el('strong', null, token.slice(2, -2)));
      } else if (token.startsWith('`')) {
        nodes.push(el('code', { class: 'md-inline-code' }, token.slice(1, -1)));
      } else {
        nodes.push(el('em', null, token.slice(1, -1)));
      }
      lastIndex = pattern.lastIndex;
    }
    if (lastIndex < text.length) nodes.push(document.createTextNode(text.slice(lastIndex)));
    return nodes;
  }

  // ------------------------------------------------------------------
  // Formatters / utils
  // ------------------------------------------------------------------

  function formatCompactNumber(n) {
    n = Number(n) || 0;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}k`;
    return String(n);
  }

  function humanizeSeconds(seconds) {
    seconds = Number(seconds) || 0;
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = seconds / 60;
    if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`;
    const hours = minutes / 60;
    if (hours < 24) return `${hours.toFixed(hours < 10 ? 1 : 0)}h`;
    return `${(hours / 24).toFixed(hours / 24 < 10 ? 1 : 0)}d`;
  }

  function timeAgo(isoString) {
    if (!isoString) return 'unknown';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return 'unknown';
    const seconds = (Date.now() - date.getTime()) / 1000;
    if (seconds < 45) return 'just now';
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return `${Math.round(seconds / 86400)}d ago`;
  }

  function truncate(text, max) {
    if (!text) return '';
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  }

  function hashColor(name) {
    let hash = 0;
    const str = String(name || '');
    for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    return `hsl(${hash % 360}, 62%, 45%)`;
  }

  function parseLabels(text) {
    // Server lowercases labels on save — mirror that here so the drawer
    // never shows casing the board chips won't.
    return String(text || '')
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
  }

  function canonicalStateName(lowerName) {
    const columns = (state.board && state.board.columns) || (state.workflow && state.workflow.columns) || [];
    const found = columns.find((c) => c.name.toLowerCase() === String(lowerName).toLowerCase());
    return found ? found.name : lowerName;
  }

  function isLearnState(name) {
    return String(name || '').trim().toLowerCase() === 'learn';
  }

  // ------------------------------------------------------------------
  // Toast system
  // ------------------------------------------------------------------

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = el('div', { class: `toast toast-${type}`, role: 'status' }, message);
    const dismiss = () => {
      toast.classList.add('toast-out');
      setTimeout(() => toast.remove(), 160);
    };
    const timer = setTimeout(dismiss, 4000);
    toast.addEventListener('click', () => {
      clearTimeout(timer);
      dismiss();
    });
    container.appendChild(toast);
  }

  // ------------------------------------------------------------------
  // Overlays: modal, confirm dialog, popover menu, drawer
  // ------------------------------------------------------------------

  function openModal(contentNode, size) {
    closeModal();
    const backdrop = el('div', {
      class: 'modal-backdrop',
      onClick: (e) => { if (e.target === backdrop) closeModal(); },
    });
    const modal = el('div', { class: `modal${size === 'lg' ? ' modal-lg' : ''}`, role: 'dialog', 'aria-modal': 'true' }, [contentNode]);
    backdrop.appendChild(modal);
    document.getElementById('overlay-root').appendChild(backdrop);
    requestAnimationFrame(() => backdrop.classList.add('open'));
    state.openModalBackdrop = backdrop;
    return modal;
  }

  function closeModal() {
    if (state.openModalBackdrop) {
      state.openModalBackdrop.remove();
      state.openModalBackdrop = null;
    }
  }

  function openFormModal({ title, body, submitLabel = 'Save', onSubmit, size }) {
    const errorBox = el('div', { class: 'modal-error', style: 'display:none;' });
    const submitBtn = el('button', { class: 'btn btn-primary', type: 'submit' }, submitLabel);
    const form = el(
      'form',
      {
        class: 'modal-form',
        onSubmit: async (e) => {
          e.preventDefault();
          submitBtn.disabled = true;
          errorBox.style.display = 'none';
          try {
            await onSubmit();
            closeModal();
          } catch (err) {
            errorBox.textContent = err.message || 'Something went wrong';
            errorBox.style.display = 'block';
          } finally {
            submitBtn.disabled = false;
          }
        },
      },
      [
        el('div', { class: 'modal-header' }, [
          el('h2', null, title),
          el('button', { class: 'btn-icon modal-close', type: 'button', 'aria-label': 'Close', onClick: closeModal }, '✕'),
        ]),
        el('div', { class: 'modal-body' }, [body, errorBox]),
        el('div', { class: 'modal-footer' }, [
          el('button', { class: 'btn btn-ghost', type: 'button', onClick: closeModal }, 'Cancel'),
          submitBtn,
        ]),
      ]
    );
    openModal(form, size);
    const firstInput = form.querySelector('input, textarea, select');
    if (firstInput) firstInput.focus();
  }

  function confirmDialog(message) {
    return new Promise((resolve) => {
      let resolved = false;
      const finish = (value) => {
        if (resolved) return;
        resolved = true;
        closeModal();
        resolve(value);
      };
      const content = el('div', { class: 'modal-form' }, [
        el('div', { class: 'modal-header' }, [
          el('h2', null, 'Are you sure?'),
          el('button', { class: 'btn-icon modal-close', 'aria-label': 'Close', onClick: () => finish(false) }, '✕'),
        ]),
        el('div', { class: 'modal-body' }, el('p', { class: 'confirm-message' }, message)),
        el('div', { class: 'modal-footer' }, [
          el('button', { class: 'btn btn-ghost', onClick: () => finish(false) }, 'Cancel'),
          el('button', { class: 'btn btn-danger', onClick: () => finish(true) }, 'Delete'),
        ]),
      ]);
      openModal(content);
    });
  }

  function closeAnyMenu() {
    if (state.openMenu) {
      state.openMenu.remove();
      state.openMenu = null;
    }
  }

  function openColumnMenu(col, anchor) {
    closeAnyMenu();
    const rect = anchor.getBoundingClientRect();
    const menu = el('div', {
      class: 'popover-menu',
      style: `top:${rect.bottom + 4}px; left:${Math.max(8, rect.right - 180)}px;`,
    });
    const items = [
      { label: 'Rename', action: () => openRenameColumnModal(col) },
      { label: 'Edit description', action: () => openEditDescriptionModal(col) },
    ];
    if (col.has_prompt) items.push({ label: 'Edit prompt', action: () => openPromptEditorModal(col.name) });
    items.push({ label: 'Delete', danger: true, action: () => deleteColumn(col) });
    for (const item of items) {
      menu.appendChild(
        el(
          'button',
          {
            class: `popover-item${item.danger ? ' danger' : ''}`,
            onClick: () => {
              closeAnyMenu();
              item.action();
            },
          },
          item.label
        )
      );
    }
    document.getElementById('overlay-root').appendChild(menu);
    state.openMenu = menu;
    setTimeout(() => document.addEventListener('click', closeAnyMenu, { once: true }), 0);
  }

  function ensureDrawerScaffold() {
    let backdrop = document.getElementById('drawer-backdrop');
    if (!backdrop) {
      const drawer = el('div', { id: 'drawer-panel', class: 'drawer', role: 'dialog', 'aria-modal': 'true', onClick: (e) => e.stopPropagation() });
      backdrop = el('div', { id: 'drawer-backdrop', class: 'drawer-backdrop', onClick: closeDrawer }, [drawer]);
      document.getElementById('overlay-root').appendChild(backdrop);
    }
    return backdrop;
  }

  function closeDrawer() {
    const backdrop = document.getElementById('drawer-backdrop');
    if (!backdrop) return;
    backdrop.classList.remove('open');
    const drawer = document.getElementById('drawer-panel');
    if (drawer) drawer.classList.remove('open');
    state.drawerIssue = null;
  }

  // ------------------------------------------------------------------
  // Shared form field builders
  // ------------------------------------------------------------------

  function field(labelText, node) {
    return el('label', { class: 'form-group' }, [el('span', { class: 'form-label' }, labelText), node]);
  }

  function fieldRow(children) {
    return el('div', { class: 'form-row' }, children);
  }

  function buildPrioritySelect(current) {
    const options = [el('option', { value: '', selected: current == null }, 'No priority')];
    for (const key of Object.keys(PRIORITY_META)) {
      const meta = PRIORITY_META[key];
      options.push(el('option', { value: key, selected: current != null && String(current) === key }, `${meta.short} ${meta.label}`));
    }
    return el('select', { class: 'select' }, options);
  }

  function buildStateSelect(current) {
    const columns = (state.board && state.board.columns) || [];
    return el('select', { class: 'select' }, columns.map((c) => el('option', { value: c.name, selected: c.name === current }, c.name)));
  }

  function buildAgentSelect(current) {
    const kinds = (state.board && state.board.board.agent_kinds) || [];
    const options = [el('option', { value: '', selected: !current }, 'default')];
    for (const kind of kinds) options.push(el('option', { value: kind, selected: kind === current }, kind));
    return el('select', { class: 'select' }, options);
  }

  // ------------------------------------------------------------------
  // Workflow mutation helpers (shared by Board column menu + Workflow page)
  // ------------------------------------------------------------------

  async function mutateWorkflowStates(mutator) {
    const wf = await api.getWorkflow();
    const specs = wf.columns.map((c) => ({ name: c.name, description: c.description, terminal: c.terminal }));
    const updated = mutator(specs);
    return api.putWorkflowStates(updated);
  }

  function migrationSummary(result) {
    const migratedCount = Object.keys(result.migrated || {}).length;
    const parts = [];
    if (Object.keys(result.renamed || {}).length) parts.push(`renamed ${Object.keys(result.renamed).length}`);
    if ((result.removed || []).length) parts.push(`removed ${result.removed.length}`);
    if ((result.added || []).length) parts.push(`added ${result.added.length}`);
    if (migratedCount) parts.push(`migrated ${migratedCount} ticket${migratedCount === 1 ? '' : 's'}`);
    return parts.length ? `Workflow updated: ${parts.join(', ')}` : 'Workflow updated';
  }

  function openRenameColumnModal(col) {
    const nameInput = el('input', { class: 'input', type: 'text', value: col.name, required: true });
    openFormModal({
      title: 'Rename column',
      body: field('Column name', nameInput),
      onSubmit: async () => {
        const newName = nameInput.value.trim();
        if (!newName) throw new Error('Column name is required');
        if (newName === col.name) return;
        const result = await mutateWorkflowStates((specs) =>
          specs.map((s) => (s.name === col.name ? { ...s, name: newName, previous_name: col.name } : s))
        );
        showToast(migrationSummary(result), 'success');
        await refreshBoard();
      },
    });
  }

  function openEditDescriptionModal(col) {
    const textarea = el('textarea', { class: 'textarea', rows: 4 }, col.description || '');
    openFormModal({
      title: `Edit description — ${col.name}`,
      body: field('Description', textarea),
      onSubmit: async () => {
        await mutateWorkflowStates((specs) => specs.map((s) => (s.name === col.name ? { ...s, description: textarea.value } : s)));
        showToast('Column description updated', 'success');
        await refreshBoard();
      },
    });
  }

  async function deleteColumn(col) {
    const ok = await confirmDialog(`Delete column "${col.name}"? Tickets in it will move to the fallback state.`);
    if (!ok) return;
    try {
      const result = await mutateWorkflowStates((specs) => specs.filter((s) => s.name !== col.name));
      showToast(migrationSummary(result), 'success');
      await refreshBoard();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openAddColumnModal() {
    const nameInput = el('input', { class: 'input', type: 'text', placeholder: 'e.g. In Review', required: true });
    const descInput = el('textarea', { class: 'textarea', rows: 3, placeholder: 'Optional description' });
    const terminalCheckbox = el('input', { type: 'checkbox', id: 'new-col-terminal' });
    const body = el('div', { class: 'form-stack' }, [
      field('Column name', nameInput),
      field('Description', descInput),
      el('div', { class: 'form-row-inline' }, [terminalCheckbox, el('label', { for: 'new-col-terminal' }, 'Terminal column (no agent work happens here)')]),
    ]);
    openFormModal({
      title: 'Add column',
      submitLabel: 'Add column',
      body,
      onSubmit: async () => {
        const name = nameInput.value.trim();
        if (!name) throw new Error('Column name is required');
        const result = await mutateWorkflowStates((specs) => [...specs, { name, description: descInput.value, terminal: terminalCheckbox.checked }]);
        showToast(migrationSummary(result), 'success');
        await refreshBoard();
      },
    });
  }

  async function openPromptEditorModal(stateName) {
    const modalBody = el('div', { class: 'form-hint' }, 'Loading…');
    const content = el('div', { class: 'modal-form prompt-modal-form' }, [
      el('div', { class: 'modal-header' }, [
        el('h2', null, `Edit prompt — ${stateName}`),
        el('button', { class: 'btn-icon modal-close', 'aria-label': 'Close', onClick: closeModal }, '✕'),
      ]),
      el('div', { class: 'modal-body prompt-modal-content' }, modalBody),
    ]);
    openModal(content, 'lg');
    try {
      const data = await api.getPrompt(stateName);
      clearNode(modalBody);
      modalBody.className = '';
      const errorBox = el('div', { class: 'modal-error', style: 'display:none;' });
      const textarea = el('textarea', { class: 'textarea prompt-textarea', spellcheck: false }, data.content);
      const saveBtn = el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          saveBtn.disabled = true;
          errorBox.style.display = 'none';
          try {
            await api.putPrompt(stateName, textarea.value);
            showToast('Prompt saved', 'success');
            closeModal();
          } catch (err) {
            errorBox.textContent = err.message;
            errorBox.style.display = 'block';
          } finally {
            saveBtn.disabled = false;
          }
        },
      }, 'Save');
      modalBody.appendChild(el('div', { class: 'prompt-path' }, data.path));
      modalBody.appendChild(el('div', { class: 'banner banner-info' }, 'Agents pick this up on their next dispatch.'));
      modalBody.appendChild(textarea);
      modalBody.appendChild(errorBox);
      content.appendChild(el('div', { class: 'modal-footer' }, [el('button', { class: 'btn btn-ghost', onClick: closeModal }, 'Cancel'), saveBtn]));
    } catch (err) {
      clearNode(modalBody);
      modalBody.className = 'empty-state';
      modalBody.appendChild(document.createTextNode(`No prompt configured: ${err.message}`));
    }
  }

  // ------------------------------------------------------------------
  // Router
  // ------------------------------------------------------------------

  function currentRoute() {
    const hash = location.hash.replace(/^#\/?/, '');
    return ROUTES.includes(hash) ? hash : 'board';
  }

  function navigate(route) {
    location.hash = `/${route}`;
  }

  function updateSidebarActive() {
    document.querySelectorAll('.nav-item').forEach((a) => {
      const isActive = a.dataset.route === state.route;
      a.classList.toggle('active', isActive);
      if (isActive) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  function renderRoute() {
    const view = document.getElementById('view');
    clearNode(view);
    closeModal();
    closeAnyMenu();
    closeDrawer();
    switch (state.route) {
      case 'board':
        renderBoardPage(view);
        break;
      case 'stats':
        renderStatsPage(view);
        break;
      case 'workflow':
        renderWorkflowPage(view);
        break;
      case 'settings':
        renderSettingsPage(view);
        break;
      default:
        renderBoardPage(view);
    }
  }

  function handleRouteChange() {
    state.route = currentRoute();
    updateSidebarActive();
    renderRoute();
  }

  window.addEventListener('hashchange', handleRouteChange);

  // ------------------------------------------------------------------
  // Sidebar connection indicator
  // ------------------------------------------------------------------

  function updateConnectionIndicator() {
    const dot = document.getElementById('conn-dot');
    const text = document.getElementById('conn-text');
    dot.classList.toggle('online', state.connected);
    dot.classList.toggle('offline', !state.connected);
    if (!state.connected) {
      text.textContent = 'Orchestrator unreachable';
      return;
    }
    const live = (state.board && state.board.live) || {};
    let running = 0;
    let retrying = 0;
    for (const key in live) {
      if (live[key].status === 'running') running++;
      else if (live[key].status === 'retrying') retrying++;
    }
    text.textContent = `${running} running · ${retrying} retrying`;
  }

  // ------------------------------------------------------------------
  // Skeletons
  // ------------------------------------------------------------------

  function buildBoardSkeleton() {
    const grid = el('div', { class: 'board-columns' });
    for (let i = 0; i < 4; i++) {
      const col = el('div', { class: 'column skeleton-column' });
      col.appendChild(el('div', { class: 'skeleton skeleton-title' }));
      for (let j = 0; j < 3; j++) col.appendChild(el('div', { class: 'skeleton skeleton-card' }));
      grid.appendChild(col);
    }
    return grid;
  }

  function buildSkeletonBlock() {
    return el('div', { class: 'skeleton skeleton-block' });
  }

  function buildStatsSkeleton() {
    return el('div', { class: 'stat-grid' }, Array.from({ length: 6 }, () => el('div', { class: 'skeleton skeleton-tile' })));
  }

  // ------------------------------------------------------------------
  // Page: Board
  // ------------------------------------------------------------------

  function renderBoardPage(container) {
    const page = el('div', { class: 'page page-board' });
    page.appendChild(buildBoardTopbar());
    const scroll = el('div', { class: 'board-scroll', id: 'board-scroll' });
    page.appendChild(scroll);
    container.appendChild(page);
    if (!state.board) scroll.appendChild(buildBoardSkeleton());
    else renderBoardColumns(scroll);
  }

  function buildBoardTopbar() {
    const readOnly = Boolean(state.board && state.board.board.read_only);
    const hasTerminalColumns = Boolean(state.board && state.board.columns.some((c) => c.terminal));
    const search = el('input', {
      type: 'text',
      id: 'board-search',
      class: 'input search-input',
      placeholder: 'Search issues…',
      value: state.search,
      oninput: (e) => {
        state.search = e.target.value;
        renderBoardColumns(document.getElementById('board-scroll'));
      },
    });
    const rightControls = [];
    if (hasTerminalColumns) rightControls.push(buildBoardScopeToggle());
    if (!readOnly) rightControls.push(el('button', { class: 'btn btn-primary', onClick: () => openIssueModal() }, '+ New Issue'));
    const bar = el('div', { class: 'topbar' }, [
      el('div', { class: 'topbar-left' }, [search]),
      el('div', { class: 'topbar-right' }, rightControls),
    ]);
    if (!readOnly) return bar;
    return el('div', { class: 'topbar-wrap' }, [el('div', { class: 'banner banner-info' }, 'Linear/Jira boards are read-only here.'), bar]);
  }

  function buildBoardScopeToggle() {
    const options = [
      ['active', 'Active'],
      ['all', 'All'],
    ];
    return el('div', { class: 'segmented board-scope-toggle', role: 'group', 'aria-label': 'Board columns' }, options.map(([value, label]) =>
      el('button', {
        class: `segmented-btn${state.boardScope === value ? ' active' : ''}`,
        type: 'button',
        onClick: () => {
          state.boardScope = value;
          state.mobileColumnIndex = 0;
          renderRoute();
        },
      }, label),
    ));
  }

  function matchesSearch(issue, query) {
    if (issue.identifier.toLowerCase().includes(query)) return true;
    if (issue.title.toLowerCase().includes(query)) return true;
    return issue.labels.some((l) => l.toLowerCase().includes(query));
  }

  function activeColumns(columns) {
    const active = columns.filter((c) => !c.terminal);
    return active.length ? active : columns;
  }

  function visibleBoardColumns(columns) {
    return state.boardScope === 'all' ? columns : activeColumns(columns);
  }

  function isMobileBoardViewport() {
    return window.matchMedia('(max-width: 720px)').matches;
  }

  function buildMobileLaneTabs(columns) {
    const maxIndex = Math.max(columns.length - 1, 0);
    if (state.mobileColumnIndex > maxIndex) state.mobileColumnIndex = maxIndex;
    return el('div', { class: 'mobile-lane-tabs', role: 'tablist', 'aria-label': 'Active lanes' },
      columns.map((col, index) => el('button', {
        class: `mobile-lane-tab${index === state.mobileColumnIndex ? ' active' : ''}`,
        type: 'button',
        role: 'tab',
        'aria-selected': index === state.mobileColumnIndex ? 'true' : 'false',
        onClick: () => {
          state.mobileColumnIndex = index;
          renderBoardColumns(document.getElementById('board-scroll'));
        },
      }, col.name))
    );
  }

  function renderBoardColumns(scrollEl) {
    if (!scrollEl) return;
    clearNode(scrollEl);
    if (!state.board) {
      scrollEl.appendChild(buildBoardSkeleton());
      return;
    }
    const { columns, issues, live, board } = state.board;
    const query = state.search.trim().toLowerCase();
    const filtered = query ? issues.filter((issue) => matchesSearch(issue, query)) : issues;
    const byColumn = new Map(columns.map((c) => [c.name, []]));
    for (const issue of filtered) {
      const bucket = byColumn.get(issue.state);
      if (bucket) bucket.push(issue);
    }
    const visibleColumns = visibleBoardColumns(columns);
    const mobileSingleLane = isMobileBoardViewport() && state.boardScope !== 'all';
    const layout = el('div', { class: `board-layout${state.boardScope === 'all' ? ' all-columns' : ''}${mobileSingleLane ? ' mobile-single-lane' : ''}` });
    const grid = el('div', { class: 'board-columns' });
    if (mobileSingleLane) layout.appendChild(buildMobileLaneTabs(visibleColumns));
    const columnsToRender = mobileSingleLane
      ? visibleColumns.slice(state.mobileColumnIndex, state.mobileColumnIndex + 1)
      : visibleColumns;
    for (const col of columnsToRender) grid.appendChild(buildColumnEl(col, byColumn.get(col.name) || [], live, board.read_only));
    if (!board.read_only && !mobileSingleLane) grid.appendChild(el('div', { class: 'add-column-ghost', onClick: openAddColumnModal }, '+ Add column'));
    layout.appendChild(grid);
    if (state.boardScope !== 'all') {
      const terminalGroups = columns
        .filter((col) => col.terminal)
        .map((col) => ({ col, issues: byColumn.get(col.name) || [] }))
        .filter((row) => row.issues.length > 0);
      if (terminalGroups.length) layout.appendChild(buildTerminalSectionEl(terminalGroups, live, board.read_only));
    }
    scrollEl.appendChild(layout);
  }

  function buildTerminalSectionEl(groups, live, readOnly) {
    const total = groups.reduce((sum, row) => sum + row.issues.length, 0);
    const section = el('section', { class: 'terminal-section', 'aria-label': 'Terminal states' });
    section.appendChild(el('div', { class: 'terminal-section-header' }, [
      el('div', { class: 'terminal-section-title' }, 'Review and parked'),
      el('span', { class: 'terminal-total' }, String(total)),
    ]));
    const body = el('div', { class: 'terminal-groups' });
    for (const { col, issues } of groups) body.appendChild(buildTerminalGroupEl(col, issues, live, readOnly));
    section.appendChild(body);
    return section;
  }

  function buildTerminalGroupEl(col, issues, live, readOnly) {
    const group = el('div', { class: 'terminal-group' });
    group.appendChild(el('div', { class: 'terminal-group-header' }, [
      el('div', { class: 'column-title-wrap' }, [
        el('span', { class: 'state-dot', style: `background:${hashColor(col.name)}` }),
        el('span', { class: 'column-title' }, col.name),
      ]),
      el('span', { class: 'column-count' }, String(issues.length)),
    ]));
    const body = el('div', { class: 'terminal-card-list' });
    for (const issue of issues) {
      const card = buildCardEl(issue, live[issue.identifier], readOnly);
      card.classList.add('terminal-card');
      body.appendChild(card);
    }
    group.appendChild(body);
    return group;
  }

  function buildColumnEl(col, issues, live, readOnly) {
    const dot = el('span', { class: 'state-dot', style: `background:${hashColor(col.name)}` });
    const actions = [];
    if (!readOnly) {
      actions.push(el('button', { class: 'btn-icon', title: 'New issue', 'aria-label': `New issue in ${col.name}`, onClick: () => openIssueModal({ state: col.name }) }, '+'));
      actions.push(el('button', { class: 'btn-icon', title: 'Column menu', 'aria-label': `${col.name} column menu`, onClick: (e) => { e.stopPropagation(); openColumnMenu(col, e.currentTarget); } }, '⋯'));
    }
    const header = el('div', { class: 'column-header' }, [
      el('div', { class: 'column-title-wrap' }, [dot, el('span', { class: 'column-title' }, col.name), el('span', { class: 'column-count' }, String(issues.length))]),
      el('div', { class: 'column-actions' }, actions),
    ]);
    const body = el('div', { class: 'column-body' });
    for (const issue of issues) body.appendChild(buildCardEl(issue, live[issue.identifier], readOnly));
    const column = el('div', { class: `column${col.terminal ? ' terminal' : ''}` }, [header, body]);
    // Drop zone is the whole column (header + empty space included) — an
    // empty column's body has almost no height, so a body-only listener
    // makes cards impossible to drop onto empty lanes.
    column.addEventListener('dragover', (e) => { e.preventDefault(); body.classList.add('drag-over'); });
    column.addEventListener('dragleave', (e) => { if (!column.contains(e.relatedTarget)) body.classList.remove('drag-over'); });
    column.addEventListener('drop', (e) => {
      e.preventDefault();
      body.classList.remove('drag-over');
      if (!readOnly) handleCardDrop(e, col.name);
    });
    return column;
  }

  function handleCardDrop(e, targetState) {
    const identifier = e.dataTransfer.getData('text/plain');
    if (!identifier) return;
    const issue = state.board.issues.find((i) => i.identifier === identifier);
    if (!issue || issue.state === targetState) return;
    const previousState = issue.state;
    issue.state = targetState;
    renderBoardColumns(document.getElementById('board-scroll'));
    api.patchIssue(identifier, { state: targetState }).catch((err) => {
      issue.state = previousState;
      renderBoardColumns(document.getElementById('board-scroll'));
      showToast(`Could not move ${identifier}: ${err.message}`, 'error');
    });
  }

  function buildAttentionBadge(attention) {
    if (!attention) return null;
    return el('span', {
      class: `chip-attention attention-${attention.kind || 'info'}`,
      title: attention.message || attention.label || 'Attention required',
    }, attention.label || 'Attention');
  }

  function buildCardEl(issue, liveEntry, readOnly) {
    const card = el('div', {
      class: `card${liveEntry && liveEntry.paused ? ' paused' : ''}`,
      draggable: !readOnly,
      onClick: () => openDrawer(issue.identifier),
    });
    if (!readOnly) {
      card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', issue.identifier);
        card.classList.add('dragging');
      });
      card.addEventListener('dragend', () => card.classList.remove('dragging'));
    }
    card.appendChild(el('div', { class: 'card-id' }, issue.identifier));
    card.appendChild(el('div', { class: 'card-title' }, issue.title));
    const badges = el('div', { class: 'card-badges' });
    if (issue.priority != null && PRIORITY_META[issue.priority]) {
      const meta = PRIORITY_META[issue.priority];
      badges.appendChild(el('span', { class: `badge-priority ${meta.className}` }, `${meta.short} ${meta.label}`));
    }
    for (const label of issue.labels) badges.appendChild(el('span', { class: 'chip-label' }, label));
    if (issue.agent_kind) badges.appendChild(el('span', { class: 'chip-agent' }, issue.agent_kind));
    const attentionBadge = buildAttentionBadge(issue.attention);
    if (attentionBadge) badges.appendChild(attentionBadge);
    if (badges.childNodes.length) card.appendChild(badges);
    if (!readOnly && isLearnState(issue.state) && !liveEntry) {
      card.appendChild(el('button', {
        class: 'btn btn-ghost btn-sm card-action',
        onClick: async (e) => {
          e.stopPropagation();
          await runControlAction(api.skipLearn, issue.identifier, 'Skipped Learn');
        },
      }, 'Skip Learn'));
    }
    if (liveEntry) card.appendChild(buildLiveRow(liveEntry));
    return card;
  }

  function buildLiveRow(liveEntry) {
    const statusLine = el('div', { class: 'live-status-line' });
    if (liveEntry.status === 'retrying') {
      statusLine.appendChild(el('span', { class: 'live-icon retry' }, '↻'));
      statusLine.appendChild(el('span', null, 'retrying'));
    } else {
      statusLine.appendChild(el('span', { class: 'live-dot' }));
      statusLine.appendChild(el('span', null, `turn ${liveEntry.turn_count ?? 0}`));
    }
    const totalTokens = liveEntry.tokens && liveEntry.tokens.total_tokens;
    if (totalTokens != null) statusLine.appendChild(el('span', null, `${formatCompactNumber(totalTokens)} tok`));
    if (liveEntry.paused) statusLine.appendChild(el('span', { class: 'badge-paused' }, '⏸ paused'));
    const row = el('div', { class: 'card-live' }, [statusLine]);
    if (liveEntry.last_message) row.appendChild(el('div', { class: 'live-message' }, truncate(liveEntry.last_message, 80)));
    return row;
  }

  function openIssueModal(defaults = {}) {
    const titleInput = el('input', { class: 'input', type: 'text', placeholder: 'Issue title', required: true });
    const descInput = el('textarea', { class: 'textarea', rows: 4, placeholder: 'Description (optional, markdown supported)' });
    const stateSelect = buildStateSelect(defaults.state);
    const prioritySelect = buildPrioritySelect(null);
    const labelsInput = el('input', { class: 'input', type: 'text', placeholder: 'label-one, label-two' });
    const agentSelect = buildAgentSelect('');
    const prefixInput = el('input', { class: 'input', type: 'text', placeholder: 'TASK', maxlength: 16 });

    const body = el('div', { class: 'form-stack' }, [
      field('Title', titleInput),
      field('Description', descInput),
      fieldRow([field('State', stateSelect), field('Priority', prioritySelect)]),
      field('Labels', labelsInput),
      fieldRow([field('Agent', agentSelect), field('ID prefix', prefixInput)]),
    ]);

    openFormModal({
      title: 'New issue',
      submitLabel: 'Create issue',
      body,
      onSubmit: async () => {
        const title = titleInput.value.trim();
        if (!title) throw new Error('Title is required');
        const created = await api.createIssue({
          title,
          description: descInput.value,
          state: stateSelect.value,
          priority: prioritySelect.value === '' ? null : Number(prioritySelect.value),
          labels: parseLabels(labelsInput.value),
          agent_kind: agentSelect.value,
          prefix: prefixInput.value.trim() || 'TASK',
        });
        showToast(`Created ${created.identifier}`, 'success');
        await refreshBoard();
      },
    });
  }

  async function openDrawer(identifier) {
    closeAnyMenu();
    const backdrop = ensureDrawerScaffold();
    const drawer = document.getElementById('drawer-panel');
    clearNode(drawer);
    drawer.appendChild(el('div', { class: 'skeleton skeleton-block' }));
    backdrop.classList.add('open');
    drawer.classList.add('open');
    state.drawerIssue = identifier;
    try {
      const detail = await api.getIssue(identifier);
      if (state.drawerIssue !== identifier) return;
      clearNode(drawer);
      drawer.appendChild(buildDrawerContent(detail));
    } catch (err) {
      if (state.drawerIssue !== identifier) return;
      clearNode(drawer);
      drawer.appendChild(el('div', { class: 'drawer-error' }, `Could not load ${identifier}: ${err.message}`));
    }
  }

  async function commitField(identifier, fieldName, value, onError, onSuccess) {
    try {
      await api.patchIssue(identifier, { [fieldName]: value });
      showToast('Saved', 'success');
      if (onSuccess) onSuccess();
      await refreshBoard();
    } catch (err) {
      showToast(`Could not save ${fieldName}: ${err.message}`, 'error');
      if (onError) onError();
    }
  }

  function buildDrawerContent(detail) {
    const container = el('div', { class: 'drawer-inner' });

    const titleInput = el('input', { class: 'drawer-title-input', type: 'text', value: detail.title });
    titleInput.addEventListener('blur', () => {
      const value = titleInput.value.trim();
      if (!value) { titleInput.value = detail.title; return; }
      if (value === detail.title) return;
      commitField(detail.identifier, 'title', value, () => { titleInput.value = detail.title; }, () => { detail.title = value; });
    });
    titleInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') titleInput.blur(); });

    const header = el('div', { class: 'drawer-header' }, [
      el('div', { class: 'drawer-id' }, detail.identifier),
      el('button', { class: 'btn-icon', 'aria-label': 'Close', onClick: closeDrawer }, '✕'),
    ]);

    // Every field passes a revert callback: a failed PATCH must snap the
    // control back to the last saved value, never keep showing the edit.
    const stateSelect = buildStateSelect(detail.state);
    stateSelect.addEventListener('change', () => commitField(
      detail.identifier, 'state', stateSelect.value,
      () => { stateSelect.value = detail.state; },
      () => { detail.state = stateSelect.value; },
    ));

    const prioritySelect = buildPrioritySelect(detail.priority);
    prioritySelect.addEventListener('change', () => {
      const value = prioritySelect.value === '' ? null : Number(prioritySelect.value);
      commitField(
        detail.identifier, 'priority', value,
        () => { prioritySelect.value = detail.priority == null ? '' : String(detail.priority); },
        () => { detail.priority = value; },
      );
    });

    const agentSelect = buildAgentSelect(detail.agent_kind);
    agentSelect.addEventListener('change', () => commitField(
      detail.identifier, 'agent_kind', agentSelect.value,
      () => { agentSelect.value = detail.agent_kind || ''; },
      () => { detail.agent_kind = agentSelect.value; },
    ));

    const labelsInput = el('input', { class: 'input', type: 'text', value: detail.labels.join(', ') });
    const commitLabels = () => {
      const labels = parseLabels(labelsInput.value);
      if (JSON.stringify(labels) === JSON.stringify(detail.labels)) return;
      commitField(detail.identifier, 'labels', labels, () => { labelsInput.value = detail.labels.join(', '); }, () => { detail.labels = labels; });
    };
    labelsInput.addEventListener('blur', commitLabels);
    labelsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') labelsInput.blur(); });

    const fieldsGrid = el('div', { class: 'drawer-fields' }, [
      field('State', stateSelect),
      field('Priority', prioritySelect),
      field('Agent', agentSelect),
      field('Labels', labelsInput),
    ]);

    const deleteBtn = el('button', {
      class: 'btn btn-danger-outline',
      onClick: async () => {
        const ok = await confirmDialog(`Delete ${detail.identifier}? This cannot be undone.`);
        if (!ok) return;
        try {
          await api.deleteIssue(detail.identifier);
          showToast(`Deleted ${detail.identifier}`, 'success');
          closeDrawer();
          await refreshBoard();
        } catch (err) {
          showToast(err.message, 'error');
        }
      },
    }, 'Delete issue');

    container.appendChild(header);
    container.appendChild(titleInput);
    container.appendChild(fieldsGrid);
    if (detail.attention) {
      container.appendChild(el('div', { class: `drawer-attention attention-${detail.attention.kind || 'info'}` }, [
        el('strong', null, detail.attention.label || 'Attention'),
        el('span', null, detail.attention.message || ''),
      ]));
    }
    if (!detail.live && isLearnState(detail.state)) {
      container.appendChild(el('button', {
        class: 'btn btn-ghost',
        onClick: async () => {
          await runControlAction(api.skipLearn, detail.identifier, 'Skipped Learn');
        },
      }, 'Skip Learn'));
    }
    if (detail.live) container.appendChild(buildLiveSection(detail));
    container.appendChild(buildDescriptionSection(detail));
    container.appendChild(el('div', { class: 'drawer-meta' }, [
      el('div', null, `Created ${timeAgo(detail.created_at)}`),
      el('div', null, `Updated ${timeAgo(detail.updated_at)}`),
    ]));
    container.appendChild(deleteBtn);
    return container;
  }

  function buildDescriptionSection(detail) {
    let editing = false;
    const section = el('div', { class: 'drawer-description' });
    const editBtn = el('button', { class: 'btn btn-ghost btn-sm', onClick: toggle }, 'Edit');
    const heading = el('div', { class: 'section-heading' }, [el('span', null, 'Description'), editBtn]);
    const body = el('div', { class: 'description-body' });
    section.appendChild(heading);
    section.appendChild(body);
    renderView();
    return section;

    function renderView() {
      clearNode(body);
      if (editing) {
        const textarea = el('textarea', { class: 'textarea description-editor', rows: 10 }, detail.description || '');
        const errorBox = el('div', { class: 'modal-error', style: 'display:none;' });
        const saveBtn = el('button', {
          class: 'btn btn-primary btn-sm',
          onClick: async () => {
            try {
              await api.patchIssue(detail.identifier, { description: textarea.value });
              detail.description = textarea.value;
              editing = false;
              editBtn.textContent = 'Edit';
              showToast('Description saved', 'success');
              renderView();
            } catch (err) {
              errorBox.textContent = err.message;
              errorBox.style.display = 'block';
            }
          },
        }, 'Save');
        body.appendChild(textarea);
        body.appendChild(errorBox);
        body.appendChild(el('div', { class: 'description-actions' }, [saveBtn]));
      } else if (detail.description) {
        body.appendChild(renderMarkdown(detail.description));
      } else {
        body.appendChild(el('div', { class: 'form-hint' }, 'No description'));
      }
    }

    function toggle() {
      editing = !editing;
      editBtn.textContent = editing ? 'Cancel' : 'Edit';
      renderView();
    }
  }

  function liveStat(label, value) {
    return el('div', null, [el('div', { class: 'live-stat-label' }, label), el('div', { class: 'live-stat-value' }, value)]);
  }

  function buildLiveSection(detail) {
    const live = detail.live;
    const tokens = live.tokens || {};
    const grid = el('div', { class: 'live-grid' }, [
      liveStat('Status', live.status || 'unknown'),
      liveStat('Turn', String(live.turn_count ?? 0)),
      liveStat('Tokens in', formatCompactNumber(tokens.input_tokens ?? 0)),
      liveStat('Tokens out', formatCompactNumber(tokens.output_tokens ?? 0)),
      liveStat('Tokens total', formatCompactNumber(tokens.total_tokens ?? 0)),
      liveStat('Last event', live.last_event || '—'),
    ]);
    const section = el('div', { class: 'drawer-live' }, [el('div', { class: 'section-heading' }, 'Live run'), grid]);
    if (live.last_message) section.appendChild(el('div', { class: 'live-message-block' }, live.last_message));
    const runControl = live.paused
      ? el('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { await runControlAction(api.resume, detail.identifier, 'Resumed'); } }, 'Resume')
      : el('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { await runControlAction(api.pause, detail.identifier, 'Paused'); } }, 'Pause');
    section.appendChild(el('div', { class: 'live-actions' }, [runControl]));
    return section;
  }

  async function runControlAction(fn, identifier, successMessage) {
    try {
      await fn(identifier);
      showToast(successMessage, 'success');
      await refreshBoard();
      if (state.drawerIssue === identifier) openDrawer(identifier);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function refreshBoard() {
    // Same mid-drag hold as pollBoard: an async refresh landing while the
    // user drags would rebuild the columns and cancel the HTML5 drag.
    // The 5s poll picks the data up once the drag ends.
    if (document.querySelector('.card.dragging')) return;
    try {
      const board = await api.getBoard();
      state.board = board;
      state.connected = true;
      updateConnectionIndicator();
      if (state.route === 'board') renderBoardColumns(document.getElementById('board-scroll'));
    } catch (_err) {
      // regular poll loop will surface connectivity issues
    }
  }

  // ------------------------------------------------------------------
  // Page: Stats
  // ------------------------------------------------------------------

  function renderStatsPage(container) {
    const page = el('div', { class: 'page page-stats' });
    const picker = el('div', { class: 'segmented' });
    for (const days of [7, 30, 90]) {
      picker.appendChild(el('button', { class: `segmented-btn${state.statsDays === days ? ' active' : ''}`, onClick: () => { state.statsDays = days; renderRoute(); } }, `${days}d`));
    }
    page.appendChild(el('div', { class: 'topbar' }, [el('div', { class: 'topbar-left' }, [el('h1', { class: 'page-title' }, 'Stats')]), el('div', { class: 'topbar-right' }, [picker])]));
    const content = el('div', { class: 'stats-content', id: 'stats-content' }, [buildStatsSkeleton()]);
    page.appendChild(content);
    container.appendChild(page);
    loadStats();
  }

  async function loadStats() {
    try {
      const data = await api.getStats(state.statsDays);
      const content = document.getElementById('stats-content');
      if (content) renderStatsContent(data);
    } catch (err) {
      const content = document.getElementById('stats-content');
      if (content) {
        clearNode(content);
        content.appendChild(el('div', { class: 'empty-state' }, `Could not load stats: ${err.message}`));
      }
    }
  }

  function statTile(label, value) {
    return el('div', { class: 'stat-tile' }, [el('div', { class: 'stat-value' }, value), el('div', { class: 'stat-label' }, label)]);
  }

  function chartCard(title, contentNode) {
    return el('div', { class: 'chart-card' }, [el('div', { class: 'chart-title' }, title), contentNode]);
  }

  function barChart(points, opts = {}) {
    const formatValue = opts.formatValue || formatCompactNumber;
    if (!points.length) return el('div', { class: 'chart-empty' }, 'No data');
    const maxValue = Math.max(1, ...points.map((p) => p.value || 0));
    const width = 480;
    const height = 150;
    const padding = 20;
    const barGap = 6;
    const barWidth = Math.max(4, (width - padding * 2) / points.length - barGap);
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, class: 'chart-svg', preserveAspectRatio: 'none' });
    points.forEach((p, idx) => {
      const barHeight = Math.max(((p.value || 0) / maxValue) * (height - padding * 2), 1);
      const x = padding + idx * (barWidth + barGap);
      const y = height - padding - barHeight;
      const title = svgEl('title', {}, []);
      title.textContent = `${p.label}: ${formatValue(p.value || 0)}`;
      const rect = svgEl('rect', { x, y, width: barWidth, height: barHeight, rx: 2, class: 'chart-bar' }, [title]);
      svg.appendChild(rect);
    });
    return el('div', { class: 'chart-wrap' }, [svg, el('div', { class: 'chart-labels' }, points.map((p) => el('span', { class: 'chart-label' }, p.label)))]);
  }

  function hBarChart(points, opts = {}) {
    const formatValue = opts.formatValue || formatCompactNumber;
    if (!points.length) return el('div', { class: 'chart-empty' }, 'No data');
    const maxValue = Math.max(1, ...points.map((p) => p.value || 0));
    const rows = points.map((p) => {
      const pct = Math.max(2, Math.round(((p.value || 0) / maxValue) * 100));
      return el('div', { class: 'hbar-row' }, [
        el('div', { class: 'hbar-label' }, p.label),
        el('div', { class: 'hbar-track' }, [el('div', { class: 'hbar-fill', style: `width:${pct}%` })]),
        el('div', { class: 'hbar-value' }, formatValue(p.value || 0)),
      ]);
    });
    return el('div', { class: 'hbar-chart' }, rows);
  }

  function mapStateLabels(rows, valueFn) {
    return rows.map((r) => ({ label: canonicalStateName(r.state), value: valueFn(r) }));
  }

  function buildAgentTable(rows) {
    if (!rows.length) return el('div', { class: 'chart-empty' }, 'No agent activity yet');
    const tbody = el('tbody', null, rows.map((row) => el('tr', null, [
      el('td', null, row.agent),
      el('td', null, formatCompactNumber(row.total_tokens)),
      el('td', null, String(row.turns)),
      el('td', null, String(row.runs)),
    ])));
    return el('table', { class: 'data-table' }, [
      el('thead', null, el('tr', null, ['Agent', 'Tokens', 'Turns', 'Runs'].map((h) => el('th', null, h)))),
      tbody,
    ]);
  }

  function renderStatsContent(data) {
    const content = document.getElementById('stats-content');
    clearNode(content);
    const hasEvents = data.totals.turns > 0 || data.totals.runs > 0 || data.by_day.length > 0;
    if (!hasEvents) {
      content.appendChild(el('div', { class: 'empty-state' }, 'No run activity recorded yet. Stats populate once agents start working tickets.'));
      return;
    }
    content.appendChild(el('div', { class: 'stat-grid' }, [
      statTile('Tickets done', String(data.totals.done)),
      statTile('Total tokens', formatCompactNumber(data.totals.total)),
      statTile('Turns', String(data.totals.turns)),
      statTile('Runs', String(data.totals.runs)),
      statTile('Avg cycle time', data.cycle.avg_seconds ? humanizeSeconds(data.cycle.avg_seconds) : '—'),
      statTile('Live running', String(data.live.running)),
    ]));

    const chartsGrid = el('div', { class: 'charts-grid' });
    chartsGrid.appendChild(chartCard('Tokens per day', barChart(data.by_day.map((d) => ({ label: d.date.slice(5), value: d.total })))));
    chartsGrid.appendChild(chartCard('Done per day', barChart(data.by_day.map((d) => ({ label: d.date.slice(5), value: d.done })))));
    chartsGrid.appendChild(chartCard('Tokens by column', hBarChart(mapStateLabels(data.by_state, (s) => s.total_tokens))));
    chartsGrid.appendChild(chartCard('Avg time in column', hBarChart(mapStateLabels(data.by_state, (s) => s.avg_dwell_seconds), { formatValue: humanizeSeconds })));
    content.appendChild(chartsGrid);
    content.appendChild(chartCard('By agent', buildAgentTable(data.by_agent)));
  }

  // ------------------------------------------------------------------
  // Page: Workflow
  // ------------------------------------------------------------------

  async function renderWorkflowPage(container) {
    const page = el('div', { class: 'page page-workflow' });
    page.appendChild(el('div', { class: 'topbar' }, [el('h1', { class: 'page-title' }, 'Workflow')]));
    const body = el('div', { class: 'workflow-body' }, [buildSkeletonBlock()]);
    page.appendChild(body);
    container.appendChild(page);
    try {
      const wf = await api.getWorkflow();
      state.workflow = wf;
      state.workflowDraft = wf.columns.map((c) => ({ ...c, _originalName: c.name }));
      clearNode(body);
      body.appendChild(buildWorkflowEditor());
    } catch (err) {
      clearNode(body);
      body.appendChild(el('div', { class: 'empty-state' }, `Could not load workflow: ${err.message}`));
    }
  }

  function isWorkflowDirty() {
    if (!state.workflow || !state.workflowDraft) return false;
    const normalize = (rows) => rows.map((c) => ({ name: c.name, description: c.description, terminal: c.terminal }));
    return JSON.stringify(normalize(state.workflow.columns)) !== JSON.stringify(normalize(state.workflowDraft));
  }

  function updateSaveBarVisibility() {
    const bar = document.getElementById('wf-save-bar');
    if (bar) bar.style.display = isWorkflowDirty() ? 'flex' : 'none';
  }

  function buildWorkflowEditor() {
    const wrap = el('div', { class: 'workflow-editor' });
    const list = el('div', { class: 'wf-list', id: 'wf-list' });
    wrap.appendChild(list);
    wrap.appendChild(el('button', {
      class: 'btn btn-ghost',
      onClick: () => {
        state.workflowDraft.push({ name: '', description: '', terminal: false, has_prompt: false });
        state.wfRerender();
      },
    }, '+ Add column'));
    const saveBar = el('div', { class: 'save-bar', id: 'wf-save-bar', style: 'display:none;' }, [
      el('span', null, 'You have unsaved changes'),
      el('div', { class: 'save-bar-actions' }, [
        el('button', {
          class: 'btn btn-ghost',
          onClick: () => {
            state.workflowDraft = state.workflow.columns.map((c) => ({ ...c, _originalName: c.name }));
            state.wfRerender();
          },
        }, 'Discard'),
        el('button', { class: 'btn btn-primary', onClick: saveWorkflowChanges }, 'Save changes'),
      ]),
    ]);
    wrap.appendChild(saveBar);
    wrap.appendChild(buildAgentPolicyCard(state.workflow.agent));

    state.wfRerender = () => {
      clearNode(list);
      state.workflowDraft.forEach((row) => list.appendChild(buildWfRow(row)));
      updateSaveBarVisibility();
    };
    state.wfRerender();
    return wrap;
  }

  function buildWfRow(row) {
    const nameInput = el('input', { class: 'input wf-name', type: 'text', value: row.name, oninput: (e) => { row.name = e.target.value; updateSaveBarVisibility(); } });
    const descInput = el('input', { class: 'input wf-desc', type: 'text', value: row.description, placeholder: 'Description', oninput: (e) => { row.description = e.target.value; updateSaveBarVisibility(); } });
    const terminalInput = el('input', { type: 'checkbox', checked: row.terminal, onChange: (e) => { row.terminal = e.target.checked; state.wfRerender(); } });
    const terminalToggle = el('label', { class: 'switch' }, [terminalInput, el('span', { class: 'switch-slider' })]);

    const rowChildren = [
      el('span', { class: 'drag-handle', 'aria-hidden': 'true' }, '⋮⋮'),
      nameInput,
      descInput,
      el('div', { class: 'wf-terminal-field' }, [terminalToggle, el('span', { class: 'wf-terminal-label' }, 'Terminal')]),
    ];
    if (row.has_prompt && !row.terminal) {
      rowChildren.push(el('button', { class: 'btn btn-ghost btn-sm', onClick: () => openPromptEditorModal(row.name) }, 'Edit prompt'));
    }
    rowChildren.push(el('button', {
      class: 'btn-icon danger',
      title: 'Delete column',
      'aria-label': `Delete ${row.name || 'column'}`,
      onClick: () => {
        const idx = state.workflowDraft.indexOf(row);
        if (idx >= 0) state.workflowDraft.splice(idx, 1);
        state.wfRerender();
      },
    }, '✕'));

    const rowEl = el('div', { class: 'wf-row', draggable: true }, rowChildren);
    rowEl.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', String(state.workflowDraft.indexOf(row)));
      rowEl.classList.add('dragging');
    });
    rowEl.addEventListener('dragend', () => rowEl.classList.remove('dragging'));
    rowEl.addEventListener('dragover', (e) => e.preventDefault());
    rowEl.addEventListener('drop', (e) => {
      e.preventDefault();
      const fromIdx = Number(e.dataTransfer.getData('text/plain'));
      const toIdx = state.workflowDraft.indexOf(row);
      if (Number.isNaN(fromIdx) || fromIdx === toIdx) return;
      const [moved] = state.workflowDraft.splice(fromIdx, 1);
      state.workflowDraft.splice(toIdx, 0, moved);
      state.wfRerender();
    });
    return rowEl;
  }

  async function saveWorkflowChanges() {
    const draft = state.workflowDraft;
    if (draft.some((r) => !r.name.trim())) {
      showToast('Column name cannot be empty', 'error');
      return;
    }
    const lowerNames = draft.map((r) => r.name.trim().toLowerCase());
    if (new Set(lowerNames).size !== lowerNames.length) {
      showToast('Column names must be unique', 'error');
      return;
    }
    const specs = draft.map((row) => {
      const spec = { name: row.name.trim(), description: row.description || '', terminal: Boolean(row.terminal) };
      if (row._originalName && row._originalName.toLowerCase() !== spec.name.toLowerCase()) spec.previous_name = row._originalName;
      return spec;
    });
    try {
      const result = await api.putWorkflowStates(specs);
      showToast(migrationSummary(result), 'success');
      renderRoute();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function kv(label, value) {
    return el('div', { class: 'kv-row' }, [el('span', { class: 'kv-label' }, label), el('span', { class: 'kv-value' }, value)]);
  }

  function buildAgentPolicyCard(agent) {
    return el('div', { class: 'card-panel agent-policy-card' }, [
      el('h3', null, 'Agent policy'),
      el('div', { class: 'kv-grid' }, [
        kv('Agent kind', agent.kind),
        kv('Max turns', String(agent.max_turns)),
        kv('Max concurrent', String(agent.max_concurrent_agents)),
        kv('Max attempts', String(agent.max_attempts)),
      ]),
    ]);
  }

  // ------------------------------------------------------------------
  // Page: Settings
  // ------------------------------------------------------------------

  function buildBranchSelect(current) {
    const options = [el('option', { value: '', selected: !current }, '(current branch)')];
    for (const branch of state.branches) options.push(el('option', { value: branch, selected: branch === current }, branch));
    return el('select', { class: 'select' }, options);
  }

  async function saveBranchPolicy(payload) {
    try {
      await api.putBranchPolicy(payload);
      showToast('Branch policy saved', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function buildBranchPolicyCard(wf) {
    const featureSelect = buildBranchSelect(wf.agent.feature_base_branch);
    const targetSelect = buildBranchSelect(wf.agent.auto_merge_target_branch);
    featureSelect.addEventListener('change', () => saveBranchPolicy({ feature_base_branch: featureSelect.value }));
    targetSelect.addEventListener('change', () => saveBranchPolicy({ auto_merge_target_branch: targetSelect.value }));
    return el('div', { class: 'card-panel' }, [
      el('h3', null, 'Branch policy'),
      fieldRow([field('Feature base branch', featureSelect), field('Merge target branch', targetSelect)]),
    ]);
  }

  function buildBoardInfoCard(wf) {
    return el('div', { class: 'card-panel' }, [
      el('h3', null, 'Board info'),
      el('div', { class: 'kv-grid' }, [
        kv('Workflow path', wf.workflow_path),
        kv('Tracker kind', state.board ? state.board.board.tracker_kind : '—'),
        kv('Polling interval', `${wf.polling_interval_ms} ms`),
      ]),
      el('a', { href: '/api/v1/state', target: '_blank', rel: 'noopener', class: 'link' }, 'View raw API state'),
    ]);
  }

  function buildRefreshCard() {
    const btn = el('button', {
      class: 'btn btn-primary',
      onClick: async (e) => {
        e.target.disabled = true;
        try {
          await api.refresh();
          showToast('Orchestrator refresh requested', 'success');
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          e.target.disabled = false;
        }
      },
    }, 'Refresh orchestrator now');
    return el('div', { class: 'card-panel' }, [el('h3', null, 'Manual controls'), btn]);
  }

  async function renderSettingsPage(container) {
    const page = el('div', { class: 'page page-settings' });
    page.appendChild(el('div', { class: 'topbar' }, [el('h1', { class: 'page-title' }, 'Settings')]));
    const body = el('div', { class: 'settings-body' }, [buildSkeletonBlock()]);
    page.appendChild(body);
    container.appendChild(page);
    try {
      const [wf, branchesResp, board] = await Promise.all([
        api.getWorkflow(),
        api.getBranches(),
        state.board ? Promise.resolve(state.board) : api.getBoard(),
      ]);
      state.workflow = wf;
      state.branches = branchesResp.branches;
      if (!state.board) state.board = board;
      clearNode(body);
      body.appendChild(buildBranchPolicyCard(wf));
      body.appendChild(buildBoardInfoCard(wf));
      body.appendChild(buildRefreshCard());
    } catch (err) {
      clearNode(body);
      body.appendChild(el('div', { class: 'empty-state' }, `Could not load settings: ${err.message}`));
    }
  }

  // ------------------------------------------------------------------
  // Poll loop
  // ------------------------------------------------------------------

  function isEditingFocused() {
    const active = document.activeElement;
    if (!active) return false;
    if (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA') return false;
    return Boolean(active.closest('#overlay-root'));
  }

  async function pollBoard() {
    // Hold DOM updates while the user is mid-edit (overlay input focused)
    // or mid-drag — a re-render would remove the drag-source node, which
    // silently cancels an HTML5 drag. The fetch itself still runs so the
    // connection indicator stays truthful.
    const holdRender = isEditingFocused() || Boolean(document.querySelector('.card.dragging'));
    try {
      const board = await api.getBoard();
      state.connected = true;
      if (!holdRender) {
        const firstLoad = !state.board;
        state.board = board;
        const nameEl = document.getElementById('board-name');
        if (nameEl) nameEl.textContent = board.board.name || 'symphony';
        if (state.route === 'board') {
          if (firstLoad || !document.getElementById('board-scroll')) renderRoute();
          else renderBoardColumns(document.getElementById('board-scroll'));
        }
      }
    } catch (_err) {
      state.connected = false;
    } finally {
      updateConnectionIndicator();
      setTimeout(pollBoard, 5000);
    }
  }

  // ------------------------------------------------------------------
  // Bootstrap
  // ------------------------------------------------------------------

  function wireGlobalShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (state.openModalBackdrop) { closeModal(); return; }
        if (state.openMenu) { closeAnyMenu(); return; }
        const drawerBackdrop = document.getElementById('drawer-backdrop');
        if (drawerBackdrop && drawerBackdrop.classList.contains('open')) closeDrawer();
        return;
      }
      const active = document.activeElement;
      const typing = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable);
      if (typing) return;
      if (e.key === '/') {
        if (state.route !== 'board') return;
        const search = document.getElementById('board-search');
        if (search) {
          e.preventDefault();
          search.focus();
        }
      } else if (e.key === 'n' || e.key === 'N') {
        if (state.route !== 'board' || !state.board || state.board.board.read_only) return;
        e.preventDefault();
        openIssueModal();
      }
    });
  }

  function boot() {
    wireGlobalShortcuts();
    handleRouteChange();
    pollBoard();
  }

  document.addEventListener('DOMContentLoaded', boot);

  // navigate() is reachable from the console for debugging; keep it referenced
  // so linters don't flag it as unused if a future page wires it up directly.
  void navigate;
})();
