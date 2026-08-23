/*
 * oh-my-symphony board — vanilla SPA (no build step, no framework).
 * Sections: api / state / dom helpers / markdown / utils / toast /
 * overlays (modal, drawer, popover) / shared form fields / router /
 * pages (board, stats, workflow, git, settings) / poll loop / bootstrap.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // i18n — dictionaries live in i18n.js, which loads before this file.
  // ------------------------------------------------------------------

  const t = (key, params) => window.i18n.t(key, params);

  // ------------------------------------------------------------------
  // API layer
  // ------------------------------------------------------------------

  const API_BASE = '/api/v1';

  // ------------------------------------------------------------------
  // API token (SYMPHONY_API_TOKEN mode)
  //
  // When the server sets a token, every /api/ fetch needs
  // `Authorization: Bearer <token>` and the chat WebSocket needs it as
  // `?token=` (browsers cannot set WS headers). The value lives in
  // sessionStorage — tab-scoped and cleared when the tab closes — and a
  // dismissed banner stays hidden until a *new* 401 carries information
  // (a rejected stored token), so the 5s poll cannot resurrect it.
  // ------------------------------------------------------------------

  const API_TOKEN_STORAGE_KEY = 'symphony.apiToken';

  function storedApiToken() {
    try {
      return sessionStorage.getItem(API_TOKEN_STORAGE_KEY) || null;
    } catch (_err) {
      return null;
    }
  }

  function storeApiToken(token) {
    try {
      if (token) sessionStorage.setItem(API_TOKEN_STORAGE_KEY, token);
      else sessionStorage.removeItem(API_TOKEN_STORAGE_KEY);
    } catch (_err) {
      /* storage disabled — token lasts only for this render cycle */
    }
  }

  function withAuthHeaders(headers) {
    const token = storedApiToken();
    return token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
  }

  let authBannerState = { open: false, dismissed: false };

  function handleApiUnauthorized() {
    const hadToken = Boolean(storedApiToken());
    if (hadToken) {
      // The stored token was rejected — drop it so the next save is the
      // only way forward, and un-dismiss: rejection is new information.
      storeApiToken(null);
      authBannerState.dismissed = false;
    }
    if (!authBannerState.dismissed) showApiTokenBanner(hadToken);
  }

  function showApiTokenBanner(rejected) {
    let root = document.getElementById('api-token-banner-root');
    if (!root) {
      root = el('div', { id: 'api-token-banner-root' });
      const main = document.querySelector('.main');
      if (!main) return;
      main.insertBefore(root, main.firstChild);
    }
    clearNode(root);
    const input = el('input', {
      class: 'input api-token-input',
      type: 'password',
      autocomplete: 'off',
      'aria-label': t('auth.tokenPlaceholder'),
      placeholder: t('auth.tokenPlaceholder'),
    });
    const save = el('button', { class: 'btn btn-primary btn-sm', type: 'button' }, t('auth.tokenSave'));
    save.addEventListener('click', async () => {
      const token = input.value.trim();
      if (!token) {
        input.focus();
        return;
      }
      storeApiToken(token);
      authBannerState.dismissed = false;
      hideApiTokenBanner();
      showToast(t('auth.tokenSaved'), 'success');
      // Re-run the board fetch immediately; a chat page also needs its
      // WebSocket rebuilt so the handshake carries the new ?token=.
      await refreshBoard();
      if (state.route === 'chat') renderRoute();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') save.click();
    });
    const dismiss = el('button', {
      class: 'btn-icon',
      type: 'button',
      'aria-label': t('common.close'),
      onClick: () => {
        authBannerState.dismissed = true;
        hideApiTokenBanner();
      },
    }, '✕');
    root.appendChild(el('div', { class: 'banner banner-info api-token-banner', role: 'alert' }, [
      el('div', { class: 'api-token-banner-copy' }, [
        el('strong', null, t('auth.tokenBannerTitle')),
        el('span', null, rejected ? t('auth.tokenRejected') : t('auth.tokenBannerHint')),
      ]),
      el('div', { class: 'api-token-banner-form' }, [input, save]),
      dismiss,
    ]));
    authBannerState.open = true;
    input.focus();
  }

  function hideApiTokenBanner() {
    authBannerState.open = false;
    const root = document.getElementById('api-token-banner-root');
    if (root) root.remove();
  }

  class ApiError extends Error {
    constructor(message, code, status, data = null) {
      super(message);
      this.code = code;
      this.status = status;
      this.data = data;
    }
  }

  async function apiRequest(path, { method = 'GET', body, headers = {} } = {}) {
    const init = { method, headers: withAuthHeaders(headers) };
    if (body !== undefined) {
      init.body = body;
      init.headers['Content-Type'] = 'application/json';
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
      if (res.status === 401) handleApiUnauthorized();
      const err = data && data.error;
      throw new ApiError(
        (err && err.message) || t('api.requestFailed', { status: res.status }),
        (err && err.code) || 'unknown_error',
        res.status,
        data
      );
    }
    return data;
  }

  const api = {
    getBoard: () => apiRequest('/board'),
    getRequests: () => apiRequest('/requests'),
    getRequestSchedule: (kind, id) => {
      const params = new URLSearchParams({ kind, id });
      return apiRequest(`/requests/schedule?${params.toString()}`);
    },
    getProjects: () => apiRequest('/projects'),
    createOrAdoptProject: (payload) => apiRequest('/projects', { method: 'POST', body: JSON.stringify(payload) }),
    openProject: (id) => apiRequest(`/projects/${encodeURIComponent(id)}/open`, { method: 'POST', body: '{}' }),
    createIssue: (payload) => apiRequest('/issues', { method: 'POST', body: JSON.stringify(payload) }),
    getIssue: (id) => apiRequest(`/issues/${encodeURIComponent(id)}`),
    patchIssue: (id, fields) => apiRequest(`/issues/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(fields) }),
    deleteIssue: (id) => apiRequest(`/issues/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    getWorkflow: () => apiRequest('/workflow'),
    getRuns: ({ issue, limit, query, status, agent } = {}) => {
      const params = new URLSearchParams();
      if (issue) params.set('issue', issue);
      if (limit != null) params.set('limit', String(limit));
      if (query) params.set('query', query);
      if (status) params.set('status', status);
      if (agent) params.set('agent', agent);
      const search = params.toString();
      return apiRequest(`/runs${search ? `?${search}` : ''}`);
    },
    getRunDetail: (runId) => apiRequest(`/runs/${encodeURIComponent(runId)}`),
    downloadRunDiagnostic: async (runId) => {
      const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/diagnostic`, {
        headers: withAuthHeaders({}),
      });
      if (!res.ok) {
        if (res.status === 401) handleApiUnauthorized();
        let message = t('api.requestFailed', { status: res.status });
        try {
          const payload = await res.json();
          message = (payload.error && payload.error.message) || message;
        } catch (_err) { /* keep the status-based message */ }
        throw new ApiError(message, 'diagnostic_download_failed', res.status);
      }
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = `symphony-run-${runId}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
    },
    putWorkflowStates: (states) => apiRequest('/workflow/states', { method: 'PUT', body: JSON.stringify({ states }) }),
    getPrompt: (stateName) => apiRequest(`/workflow/prompts/${encodeURIComponent(stateName)}`),
    putPrompt: (stateName, content) => apiRequest(`/workflow/prompts/${encodeURIComponent(stateName)}`, { method: 'PUT', body: JSON.stringify({ content }) }),
    putBranchPolicy: (payload) => apiRequest('/workflow/branch-policy', { method: 'PUT', body: JSON.stringify(payload) }),
    getLanePresets: () => apiRequest('/workflow/presets'),
    applyLanePreset: (name) => apiRequest('/workflow/presets/apply', { method: 'POST', body: JSON.stringify({ name }) }),
    putContinuousImprovement: (payload) => apiRequest('/workflow/continuous-improvement', { method: 'PUT', body: JSON.stringify(payload) }),
    getContinuousImprovementStatus: () => apiRequest('/continuous-improvement/status'),
    resetContinuousImprovementTurns: () => apiRequest('/workflow/continuous-improvement/reset-turns', { method: 'POST' }),
    getBranches: () => apiRequest('/git/branches'),
    getGitLog: ({ branch, limit } = {}) => {
      const params = new URLSearchParams();
      if (branch) params.set('branch', branch);
      if (limit != null) params.set('limit', String(limit));
      const query = params.toString();
      return apiRequest(`/git/log${query ? `?${query}` : ''}`);
    },
    getTaskBranches: () => apiRequest('/git/task-branches'),
    getGitCompare: ({ branch, target } = {}) => {
      const params = new URLSearchParams();
      params.set('branch', branch);
      if (target) params.set('target', target);
      return apiRequest(`/git/compare?${params.toString()}`);
    },
    getGitDiff: ({ branch, target, path, commit } = {}) => {
      const params = new URLSearchParams();
      if (commit) params.set('commit', commit);
      if (branch) params.set('branch', branch);
      if (target) params.set('target', target);
      if (path) params.set('path', path);
      return apiRequest(`/git/diff?${params.toString()}`);
    },
    postGitMerge: (payload) => apiRequest('/git/merge', { method: 'POST', body: JSON.stringify(payload) }),
    getGitRemoteStatus: () => apiRequest('/git/remote-status'),
    postGitBranchDelete: (payload) => apiRequest('/git/branch/delete', { method: 'POST', body: JSON.stringify(payload) }),
    postGitPush: (payload) => apiRequest('/git/push', { method: 'POST', body: JSON.stringify(payload) }),
    postGitPullRequest: (payload) => apiRequest('/git/pr', { method: 'POST', body: JSON.stringify(payload) }),
    getChatSessions: () => apiRequest('/chat/sessions'),
    createChatSession2: (payload) => apiRequest('/chat/sessions', { method: 'POST', body: JSON.stringify(payload) }),
    getChatSessionById: (id) => apiRequest(`/chat/sessions/${encodeURIComponent(id)}`),
    patchChatSessionById: (id, payload) => apiRequest(`/chat/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
    deleteChatSessionById: (id, { forget } = {}) => apiRequest(`/chat/sessions/${encodeURIComponent(id)}${forget ? '?forget=true' : ''}`, { method: 'DELETE' }),
    postChatMessageTo: (id, payload) => apiRequest(`/chat/sessions/${encodeURIComponent(id)}/message`, { method: 'POST', body: JSON.stringify(payload) }),
    selectChatProjectSetup: (sessionId, actionId, confirmationToken) => apiRequest(
      `/chat/sessions/${encodeURIComponent(sessionId)}/project-setup/${encodeURIComponent(actionId)}/select`,
      {
        method: 'POST',
        body: '{}',
        headers: { 'X-Symphony-Chat-Confirmation': confirmationToken },
      }
    ),
    reattachChatSession: (id, confirmationToken) => apiRequest(
      `/chat/sessions/${encodeURIComponent(id)}/reattach`,
      {
        method: 'POST',
        body: JSON.stringify({ confirmation_token: confirmationToken }),
      }
    ),
    getChatSession: () => apiRequest('/chat/session'),
    createChatSession: (payload) => apiRequest('/chat/session', { method: 'POST', body: JSON.stringify(payload) }),
    patchChatSession: (payload) => apiRequest('/chat/session', { method: 'PATCH', body: JSON.stringify(payload) }),
    deleteChatSession: () => apiRequest('/chat/session', { method: 'DELETE' }),
    postChatMessage: (payload) => apiRequest('/chat/message', { method: 'POST', body: JSON.stringify(payload) }),
    getStats: (days) => apiRequest(`/stats?days=${encodeURIComponent(days)}`),
    pause: (id) => apiRequest(`/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
    resume: (id) => apiRequest(`/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
    skipDocument: (id) => apiRequest(`/${encodeURIComponent(id)}/skip-document`, { method: 'POST' }),
    recoverBlocked: (id) => apiRequest(`/issues/${encodeURIComponent(id)}/recover-blocked`, { method: 'POST' }),
    refresh: () => apiRequest('/refresh', { method: 'POST' }),
    getPreview: () => apiRequest('/preview'),
    startPreview: () => apiRequest('/preview/start', { method: 'POST', body: '{}' }),
    stopPreview: () => apiRequest('/preview/stop', { method: 'POST', body: '{}' }),
    restartPreview: () => apiRequest('/preview/restart', { method: 'POST', body: '{}' }),
  };

  // ------------------------------------------------------------------
  // State store
  // ------------------------------------------------------------------

  const ROUTES = ['board', 'runs', 'stats', 'workflow', 'git', 'chat', 'preview', 'settings'];

  const PRIORITY_META = {
    0: { label: t('priority.urgent'), short: 'P0', className: 'p0' },
    1: { label: t('priority.high'), short: 'P1', className: 'p1' },
    2: { label: t('priority.medium'), short: 'P2', className: 'p2' },
    3: { label: t('priority.low'), short: 'P3', className: 'p3' },
    4: { label: t('priority.minor'), short: 'P4', className: 'p4' },
  };

  const state = {
    route: 'board',
    board: null,
    projects: [],
    currentProject: null,
    workflow: null,
    branches: [],
    // Remotes + gh availability decide which Git page actions are usable.
    gitRemote: null,
    connected: false,
    // Timestamp of the last successful /board fetch. While polls fail,
    // the age of this stamp drives the "updated Ns ago" label and the
    // board-stale dimming, so a frozen snapshot never looks current.
    lastSuccessfulPollAt: null,
    search: '',
    boardScope: 'active',
    boardView: 'lanes',
    requestCatalog: null,
    requestSchedule: null,
    selectedRequestKey: null,
    requestLoading: false,
    requestEpoch: 0,
    mobileColumnIndex: 0,
    statsDays: 30,
    selectedRunId: null,
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

      const table = parseTableAt(lines, i);
      if (table) {
        flushList();
        i += 2;
        const rows = [];
        while (i < lines.length) {
          if (isMarkdownBlockBoundary(lines[i])) break;
          const cells = splitTableRow(lines[i]);
          if (!cells) break;
          rows.push(normalizeTableRow(cells, table.alignments.length));
          i++;
        }
        root.appendChild(renderTable(table.headers, table.alignments, rows));
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
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,6})\s|^```|^\s*[-*]\s|^\s*\d+[.)]\s/.test(lines[i]) &&
        !parseTableAt(lines, i)
      ) {
        paraLines.push(lines[i]);
        i++;
      }
      root.appendChild(el('p', { class: 'md-paragraph' }, renderInline(paraLines.join(' '))));
    }
    flushList();
    return root;
  }

  function isMarkdownBlockBoundary(line) {
    return /^(#{1,6})\s|^```|^\s*[-*]\s|^\s*\d+[.)]\s|^\s*>/.test(line);
  }

  function parseTableAt(lines, index) {
    if (index + 1 >= lines.length) return null;
    const headers = splitTableRow(lines[index]);
    const alignments = parseTableAlignments(lines[index + 1]);
    if (!headers || !alignments || headers.length !== alignments.length) return null;
    return { headers, alignments };
  }

  function splitTableRow(line) {
    if (!line || !line.trim() || !hasUnescapedPipe(line)) return null;
    let content = line.trim();
    if (content.startsWith('|')) content = content.slice(1);
    if (content.endsWith('|') && !isEscapedCharacter(content, content.length - 1)) {
      content = content.slice(0, -1);
    }

    const cells = [];
    let cell = '';
    for (let index = 0; index < content.length; index++) {
      const char = content[index];
      if (char === '|' && !isEscapedCharacter(content, index)) {
        cells.push(cell.trim());
        cell = '';
      } else if (char === '\\' && content[index + 1] === '|' && !isEscapedCharacter(content, index)) {
        cell += '|';
        index++;
      } else {
        cell += char;
      }
    }
    cells.push(cell.trim());
    return cells;
  }

  function hasUnescapedPipe(line) {
    for (let index = 0; index < line.length; index++) {
      if (line[index] === '|' && !isEscapedCharacter(line, index)) return true;
    }
    return false;
  }

  function isEscapedCharacter(text, index) {
    let backslashes = 0;
    for (let cursor = index - 1; cursor >= 0 && text[cursor] === '\\'; cursor--) backslashes++;
    return backslashes % 2 === 1;
  }

  function parseTableAlignments(line) {
    const cells = splitTableRow(line);
    if (!cells) return null;
    const alignments = [];
    for (const rawCell of cells) {
      const marker = rawCell.replace(/\s/g, '');
      if (!/^:?-{3,}:?$/.test(marker)) return null;
      const left = marker.startsWith(':');
      const right = marker.endsWith(':');
      alignments.push(left && right ? 'center' : right ? 'right' : 'left');
    }
    return alignments;
  }

  function normalizeTableRow(cells, columnCount) {
    if (cells.length === columnCount) return cells;
    if (cells.length < columnCount) return cells.concat(Array(columnCount - cells.length).fill(''));
    return cells.slice(0, columnCount - 1).concat(cells.slice(columnCount - 1).join(' | '));
  }

  function renderTable(headers, alignments, rows) {
    const headerCells = headers.map((text, index) => el('th', {
      class: `md-table-cell md-align-${alignments[index] || 'left'}`,
      scope: 'col',
    }, renderInline(text)));
    const bodyRows = rows.map((row) => el('tr', null, row.map((text, index) => el('td', {
      class: `md-table-cell md-align-${alignments[index] || 'left'}`,
    }, renderInline(text)))));
    const table = el('table', { class: 'md-table' }, [
      el('thead', null, el('tr', null, headerCells)),
      el('tbody', null, bodyRows),
    ]);
    return el('div', { class: 'md-table-wrap' }, table);
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
    if (!isoString) return t('common.unknown');
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return t('common.unknown');
    const seconds = (Date.now() - date.getTime()) / 1000;
    if (seconds < 45) return t('common.justNow');
    if (seconds < 3600) return t('common.secondsAgo', { n: Math.round(seconds / 60) });
    if (seconds < 86400) return t('common.hoursAgo', { n: Math.round(seconds / 3600) });
    return t('common.daysAgo', { n: Math.round(seconds / 86400) });
  }

  function formatShortDateTime(isoString) {
    if (!isoString) return t('common.openEnded');
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return t('common.unknown');
    return date.toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
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

  function isDocumentState(name) {
    const state = String(name || '').trim().toLowerCase();
    // 'learn' is the legacy name of the Document lane (pre-rename boards).
    return state === 'document' || state === 'learn';
  }

  function isBlockedState(name) {
    return String(name || '').trim().toLowerCase() === 'blocked';
  }

  // ------------------------------------------------------------------
  // Toast system
  // ------------------------------------------------------------------

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    // Errors are assertive (role=alert); success/info are polite status.
    const toast = el('div', { class: `toast toast-${type}`, role: type === 'error' ? 'alert' : 'status' }, message);
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

  function openFormModal({ title, body, submitLabel = t('common.save'), onSubmit, size }) {
    const titleId = 'form-modal-title';
    const errorBox = el('div', { class: 'modal-error', style: 'display:none;', role: 'alert', 'aria-live': 'assertive' });
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
            errorBox.textContent = err.message || t('common.somethingWentWrong');
            errorBox.style.display = 'block';
          } finally {
            submitBtn.disabled = false;
          }
        },
      },
      [
        el('div', { class: 'modal-header' }, [
          el('h2', { id: titleId }, title),
          el('button', { class: 'btn-icon modal-close', type: 'button', 'aria-label': t('common.close'), onClick: closeModal }, '✕'),
        ]),
        el('div', { class: 'modal-body' }, [body, errorBox]),
        el('div', { class: 'modal-footer' }, [
          el('button', { class: 'btn btn-ghost', type: 'button', onClick: closeModal }, t('common.cancel')),
          submitBtn,
        ]),
      ]
    );
    const modal = openModal(form, size);
    modal.setAttribute('aria-labelledby', titleId);
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
          el('h2', null, t('common.areYouSure')),
          el('button', { class: 'btn-icon modal-close', 'aria-label': t('common.close'), onClick: () => finish(false) }, '✕'),
        ]),
        el('div', { class: 'modal-body' }, el('p', { class: 'confirm-message' }, message)),
        el('div', { class: 'modal-footer' }, [
          el('button', { class: 'btn btn-ghost', onClick: () => finish(false) }, t('common.cancel')),
          el('button', { class: 'btn btn-danger', onClick: () => finish(true) }, t('common.delete')),
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
      { label: t('common.rename'), action: () => openRenameColumnModal(col) },
      { label: t('board.editDescription'), action: () => openEditDescriptionModal(col) },
    ];
    if (col.has_prompt) items.push({ label: t('board.editPrompt'), action: () => openPromptEditorModal(col.name) });
    items.push({ label: t('common.delete'), danger: true, action: () => deleteColumn(col) });
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
    const options = [el('option', { value: '', selected: current == null }, t('board.noPriority'))];
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
    const options = [el('option', { value: '', selected: !current }, t('board.defaultAgent'))];
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
    if (Object.keys(result.renamed || {}).length) parts.push(t('board.migrationRenamed', { n: Object.keys(result.renamed).length }));
    if ((result.removed || []).length) parts.push(t('board.migrationRemoved', { n: result.removed.length }));
    if ((result.added || []).length) parts.push(t('board.migrationAdded', { n: result.added.length }));
    if (migratedCount) parts.push(t(migratedCount === 1 ? 'board.migrationMigratedOne' : 'board.migrationMigrated', { n: migratedCount }));
    return parts.length ? t('board.workflowUpdatedWith', { summary: parts.join(', ') }) : t('board.workflowUpdated');
  }

  function openRenameColumnModal(col) {
    const nameInput = el('input', { class: 'input', type: 'text', value: col.name, required: true });
    openFormModal({
      title: t('board.renameColumn'),
      body: field(t('board.columnName'), nameInput),
      onSubmit: async () => {
        const newName = nameInput.value.trim();
        if (!newName) throw new Error(t('board.columnNameRequired'));
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
      title: t('board.editDescriptionTitle', { name: col.name }),
      body: field(t('common.description'), textarea),
      onSubmit: async () => {
        await mutateWorkflowStates((specs) => specs.map((s) => (s.name === col.name ? { ...s, description: textarea.value } : s)));
        showToast(t('board.columnDescriptionUpdated'), 'success');
        await refreshBoard();
      },
    });
  }

  async function deleteColumn(col) {
    const ok = await confirmDialog(t('board.deleteColumnConfirm', { name: col.name }));
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
    const nameInput = el('input', { class: 'input', type: 'text', placeholder: t('board.columnNamePlaceholder'), required: true });
    const descInput = el('textarea', { class: 'textarea', rows: 3, placeholder: t('board.optionalDescription') });
    const terminalCheckbox = el('input', { type: 'checkbox', id: 'new-col-terminal' });
    const body = el('div', { class: 'form-stack' }, [
      field(t('board.columnName'), nameInput),
      field(t('common.description'), descInput),
      el('div', { class: 'form-row-inline' }, [terminalCheckbox, el('label', { for: 'new-col-terminal' }, t('board.terminalColumnHint'))]),
    ]);
    openFormModal({
      title: t('board.addColumn'),
      submitLabel: t('board.addColumn'),
      body,
      onSubmit: async () => {
        const name = nameInput.value.trim();
        if (!name) throw new Error(t('board.columnNameRequired'));
        const result = await mutateWorkflowStates((specs) => [...specs, { name, description: descInput.value, terminal: terminalCheckbox.checked }]);
        showToast(migrationSummary(result), 'success');
        await refreshBoard();
      },
    });
  }

  async function openPromptEditorModal(stateName) {
    const modalBody = el('div', { class: 'form-hint' }, t('common.loading'));
    const content = el('div', { class: 'modal-form prompt-modal-form' }, [
      el('div', { class: 'modal-header' }, [
        el('h2', null, t('board.editPromptTitle', { name: stateName })),
        el('button', { class: 'btn-icon modal-close', 'aria-label': t('common.close'), onClick: closeModal }, '✕'),
      ]),
      el('div', { class: 'modal-body prompt-modal-content' }, modalBody),
    ]);
    openModal(content, 'lg');
    try {
      const data = await api.getPrompt(stateName);
      clearNode(modalBody);
      modalBody.className = 'prompt-editor-body';
      const errorBox = el('div', { class: 'modal-error', style: 'display:none;' });
      const textarea = el('textarea', { class: 'textarea prompt-textarea', spellcheck: false }, data.content);
      const saveBtn = el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          saveBtn.disabled = true;
          errorBox.style.display = 'none';
          try {
            await api.putPrompt(stateName, textarea.value);
            showToast(t('board.promptSaved'), 'success');
            closeModal();
          } catch (err) {
            errorBox.textContent = err.message;
            errorBox.style.display = 'block';
          } finally {
            saveBtn.disabled = false;
          }
        },
      }, t('common.save'));
      modalBody.appendChild(el('div', { class: 'prompt-path' }, data.path));
      modalBody.appendChild(el('div', { class: 'banner banner-info' }, t('board.promptDispatchHint')));
      modalBody.appendChild(textarea);
      modalBody.appendChild(errorBox);
      content.appendChild(el('div', { class: 'modal-footer' }, [el('button', { class: 'btn btn-ghost', onClick: closeModal }, t('common.cancel')), saveBtn]));
    } catch (err) {
      clearNode(modalBody);
      modalBody.className = 'empty-state';
      modalBody.appendChild(document.createTextNode(t('board.noPromptConfigured', { error: err.message })));
    }
  }


  // ------------------------------------------------------------------
  // Project identity and switching
  // ------------------------------------------------------------------

  function setProjectPath(element, value) {
    if (!element) return;
    const displayValue = value || t('projects.notFileBoard');
    element.textContent = displayValue;
    element.title = value || '';
    element.dataset.fullPath = value || '';
    element.tabIndex = value ? 0 : -1;
    if (value) element.setAttribute('aria-label', value);
    else element.removeAttribute('aria-label');
  }

  function renderProjectSwitcher() {
    const selector = document.getElementById('project-selector');
    const pathEl = document.getElementById('project-current-path');
    const workflowPathEl = document.getElementById('project-workflow-path');
    const boardPathEl = document.getElementById('project-board-path');
    if (!selector) return;
    clearNode(selector);
    const current = state.currentProject;
    const registeredCurrent = current && current.id;
    if (current && !registeredCurrent) {
      selector.appendChild(el('option', { value: '', selected: true }, current.name));
    }
    for (const project of state.projects) {
      selector.appendChild(el('option', {
        value: project.id,
        selected: project.id === (current && current.id),
      }, project.name));
    }
    selector.disabled = state.projects.length === 0;
    if (current) {
      setProjectPath(pathEl, current.repo_path);
      setProjectPath(workflowPathEl, current.workflow_path);
      setProjectPath(boardPathEl, current.board_path);
    }
  }

  async function switchProject(projectId) {
    if (!projectId || projectId === (state.currentProject && state.currentProject.id)) return;
    const selector = document.getElementById('project-selector');
    if (selector) selector.disabled = true;
    try {
      const opened = await api.openProject(projectId);
      window.location.assign(opened.url);
    } catch (err) {
      showToast(err.message, 'error');
      renderProjectSwitcher();
    }
  }

  async function loadProjects() {
    try {
      const data = await api.getProjects();
      state.projects = data.projects || [];
      state.currentProject = data.current || null;
      renderProjectSwitcher();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function openManageProjectsDialog() {
    const nameInput = el('input', {
      class: 'input',
      id: 'project-name-input',
      name: 'name',
      type: 'text',
      required: true,
      autocomplete: 'off',
      placeholder: t('projects.namePlaceholder'),
    });
    const pathInput = el('input', {
      class: 'input',
      id: 'project-path-input',
      name: 'path',
      type: 'text',
      required: true,
      autocomplete: 'off',
      placeholder: t('projects.pathPlaceholder'),
    });
    const body = el('div', { class: 'project-manage-form' }, [
      el('p', { class: 'form-help' }, t('projects.createOrAdoptHint')),
      field(t('projects.nameLabel'), nameInput),
      field(t('projects.pathLabel'), pathInput),
    ]);
    openFormModal({
      title: t('projects.manageTitle'),
      body,
      submitLabel: t('projects.createOrAdopt'),
      onSubmit: async () => {
        const result = await api.createOrAdoptProject({
          name: nameInput.value.trim(),
          path: pathInput.value.trim(),
        });
        await loadProjects();
        showToast(t('projects.added', { name: result.project.name }), 'success');
      },
    });
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
    closeChatSocket();
    cancelPreviewPoll();
    cancelRunsPoll();
    switch (state.route) {
      case 'board':
        renderBoardPage(view);
        break;
      case 'runs':
        renderRunsPage(view);
        break;
      case 'stats':
        renderStatsPage(view);
        break;
      case 'workflow':
        renderWorkflowPage(view);
        break;
      case 'git':
        renderGitPage(view);
        break;
      case 'chat':
        renderChatPage(view);
        break;
      case 'preview':
        renderPreviewPage(view);
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
  // Sidebar connection indicator + board staleness
  // ------------------------------------------------------------------

  // Past this age without a successful poll the board is treated as
  // stale: content dims and stops taking pointer events so nobody acts
  // on a frozen snapshot believing it is live.
  const BOARD_STALE_MS = 15000;

  function updateConnectionIndicator() {
    const dot = document.getElementById('conn-dot');
    const text = document.getElementById('conn-text');
    dot.classList.toggle('online', state.connected);
    dot.classList.toggle('offline', !state.connected);
    updateBoardStaleness();
    if (!state.connected) {
      const age = secondsSinceLastPoll();
      text.textContent = age == null
        ? t('conn.unreachable')
        : `${t('conn.unreachable')} · ${t('conn.staleAgo', { n: age })}`;
      return;
    }
    const live = (state.board && state.board.live) || {};
    let running = 0;
    let retrying = 0;
    for (const key in live) {
      if (live[key].status === 'running') running++;
      else if (live[key].status === 'retrying') retrying++;
    }
    text.textContent = t('conn.summary', { running, retrying });
  }

  function secondsSinceLastPoll() {
    if (state.lastSuccessfulPollAt == null) return null;
    return Math.max(0, Math.round((Date.now() - state.lastSuccessfulPollAt) / 1000));
  }

  function updateBoardStaleness() {
    const main = document.querySelector('.main');
    if (!main) return;
    // Never dim before the first successful load — that state already
    // shows skeletons, and dimming them adds no information.
    const stale = !state.connected
      && state.lastSuccessfulPollAt != null
      && (Date.now() - state.lastSuccessfulPollAt) > BOARD_STALE_MS;
    main.classList.toggle('board-stale', stale);
  }

  // Pointer events on a stale board are already blocked via CSS; keyboard
  // activation must check the same flag or Enter/Space would act on data
  // the operator has been told is frozen.
  function boardIsStale() {
    const main = document.querySelector('.main');
    return Boolean(main && main.classList.contains('board-stale'));
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
    else renderBoardSurface(scroll);
  }

  function renderBoardSurface(scrollEl) {
    if (state.boardView === 'request') renderRequestView(scrollEl);
    else renderBoardColumns(scrollEl);
  }

  function buildBoardViewToggle() {
    return el('div', { class: 'segmented board-view-toggle', role: 'group', 'aria-label': t('board.viewMode') }, [
      ['lanes', t('board.viewLanes')],
      ['request', t('board.viewRequest')],
    ].map(([value, label]) => el('button', {
      class: `segmented-btn${state.boardView === value ? ' active' : ''}`,
      type: 'button',
      'aria-pressed': state.boardView === value ? 'true' : 'false',
      onClick: () => {
        if (state.boardView === value) return;
        state.requestEpoch += 1;
        state.boardView = value;
        renderRoute();
        if (value === 'request') loadRequestCatalog();
      },
    }, label)));
  }

  function buildBoardTopbar() {
    const readOnly = Boolean(state.board && state.board.board.read_only);
    const hasTerminalColumns = Boolean(state.board && state.board.columns.some((c) => c.terminal));
    const search = el('input', {
      type: 'text',
      id: 'board-search',
      class: 'input search-input',
      'aria-label': t('board.searchAria'),
      placeholder: t('board.searchPlaceholder'),
      value: state.search,
      oninput: (e) => {
        state.search = e.target.value;
        renderBoardSurface(document.getElementById('board-scroll'));
      },
    });
    const rightControls = [buildBoardViewToggle()];
    if (state.boardView === 'lanes' && hasTerminalColumns) rightControls.push(buildBoardScopeToggle());
    if (!readOnly && state.boardView === 'lanes') rightControls.push(el('button', { class: 'btn btn-primary', onClick: () => openIssueModal() }, t('board.newIssueButton')));
    const bar = el('div', { class: 'topbar' }, [
      el('div', { class: 'topbar-left' }, state.boardView === 'lanes' ? [search] : []),
      el('div', { class: 'topbar-right' }, rightControls),
    ]);
    if (!readOnly) return bar;
    return el('div', { class: 'topbar-wrap' }, [el('div', { class: 'banner banner-info' }, t('board.readOnlyTracker')), bar]);
  }

  function buildBoardScopeToggle() {
    const options = [
      ['active', t('common.active')],
      ['all', t('common.all')],
    ];
    return el('div', { class: 'segmented board-scope-toggle', role: 'group', 'aria-label': t('board.scopeGroupLabel') }, options.map(([value, label]) =>
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

  // Must stay in sync with the mobile breakpoint in style.css.
  const MOBILE_BREAKPOINT = '(max-width: 768px)';

  function isMobileBoardViewport() {
    return window.matchMedia(MOBILE_BREAKPOINT).matches;
  }

  function buildMobileLaneTabs(columns) {
    const maxIndex = Math.max(columns.length - 1, 0);
    if (state.mobileColumnIndex > maxIndex) state.mobileColumnIndex = maxIndex;
    return el('div', { class: 'mobile-lane-tabs', role: 'tablist', 'aria-label': t('board.activeLanes') },
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

  function requestKey(row) {
    return `${row.kind}:${row.id}`;
  }

  function requestViewIsCurrent(epoch, selectedKey = null) {
    return state.route === 'board'
      && state.boardView === 'request'
      && state.requestEpoch === epoch
      && (selectedKey == null || state.selectedRequestKey === selectedKey);
  }

  async function loadRequestCatalog() {
    const epoch = ++state.requestEpoch;
    state.requestLoading = true;
    renderRequestView(document.getElementById('board-scroll'));
    try {
      const catalog = await api.getRequests();
      if (!requestViewIsCurrent(epoch)) return;
      state.requestCatalog = catalog;
      const rows = catalog.requests || [];
      if (!rows.some((row) => requestKey(row) === state.selectedRequestKey)) {
        state.selectedRequestKey = rows.length ? requestKey(rows[0]) : null;
      }
      if (state.selectedRequestKey && catalog.reason !== 'unsupported_tracker') {
        await loadSelectedRequestSchedule(false, epoch);
      } else {
        state.requestSchedule = null;
      }
    } catch (err) {
      if (!requestViewIsCurrent(epoch)) return;
      state.requestCatalog = { available: false, reason: 'load_failed', error: err.message, requests: [] };
      state.requestSchedule = null;
    } finally {
      if (requestViewIsCurrent(epoch)) {
        state.requestLoading = false;
        renderRequestView(document.getElementById('board-scroll'));
      }
    }
  }

  async function loadSelectedRequestSchedule(renderLoading = true, parentEpoch = null) {
    const epoch = parentEpoch == null ? ++state.requestEpoch : parentEpoch;
    const rows = (state.requestCatalog && state.requestCatalog.requests) || [];
    const selected = rows.find((row) => requestKey(row) === state.selectedRequestKey);
    if (!selected) return;
    const selectedKey = requestKey(selected);
    if (renderLoading) {
      state.requestLoading = true;
      renderRequestView(document.getElementById('board-scroll'));
    }
    try {
      const schedule = await api.getRequestSchedule(selected.kind, selected.id);
      if (!requestViewIsCurrent(epoch, selectedKey)) return;
      state.requestSchedule = schedule;
    } catch (err) {
      if (!requestViewIsCurrent(epoch, selectedKey)) return;
      state.requestSchedule = { available: false, reason: 'load_failed', error: err.message };
    } finally {
      if (renderLoading && requestViewIsCurrent(epoch, selectedKey)) {
        state.requestLoading = false;
        renderRequestView(document.getElementById('board-scroll'));
        const picker = document.querySelector('.request-picker');
        if (picker) picker.focus();
      }
    }
  }

  function scheduleStatusLabel(status) {
    const labels = {
      running: t('schedule.running'),
      ready: t('schedule.ready'),
      waiting: t('schedule.waiting'),
      retrying: t('schedule.retrying'),
      successful: t('schedule.successful'),
      needs_action: t('schedule.needsAction'),
    };
    return labels[status] || status || t('schedule.waiting');
  }

  function scheduleReasonLabel(decision) {
    if (!decision) return t('schedule.notEvaluated');
    const labels = {
      not_evaluated: t('schedule.reasonNotEvaluated'),
      ready: t('schedule.reasonReady'),
      dispatched: t('schedule.reasonDispatched'),
      running: t('schedule.reasonRunning'),
      retry_scheduled: t('schedule.reasonRetry'),
      auto_triage: t('schedule.reasonAutoTriage'),
      continuous_improvement: t('schedule.reasonContinuousImprovement'),
      leased_elsewhere: t('schedule.reasonLease'),
      registry_unavailable: t('schedule.reasonRegistryUnavailable'),
      historical_release_verifier: t('schedule.reasonHistoricalVerifier'),
      claimed: t('schedule.reasonClaim'),
      paused: t('schedule.reasonPaused'),
      budget_exhausted: t('schedule.reasonBudgetExhausted'),
      finalizing: t('schedule.reasonFinalizing'),
      inactive: t('schedule.reasonInactive'),
      incomplete_identity: t('schedule.reasonIncompleteIdentity'),
      unsupported_agent: t('schedule.reasonUnsupportedAgent'),
      waiting_dependency: t('schedule.reasonDependency'),
      waiting_global_capacity: t('schedule.reasonCapacity'),
      waiting_state_capacity: t('schedule.reasonStateCapacity'),
      refused_conflict: t('schedule.reasonConflict'),
      refused_dispatch_authority: t('schedule.reasonAuthority'),
      terminal_success: t('schedule.reasonComplete'),
      terminal_needs_action: t('schedule.reasonTerminal'),
      dangling_dependency: t('schedule.reasonDangling'),
      snapshot_unavailable: t('schedule.reasonSnapshotUnavailable'),
      decision_stale: t('schedule.reasonDecisionStale'),
    };
    return labels[decision.code] || t('schedule.reasonUnknown');
  }

  function buildScheduleSummary(schedule) {
    const counts = (schedule.summary && schedule.summary.counts) || {};
    const metrics = [
      ['running', t('schedule.running')],
      ['ready', t('schedule.ready')],
      ['waiting', t('schedule.waiting')],
      ['retrying', t('schedule.retrying')],
      ['needs_action', t('schedule.needsAction')],
      ['successful', t('schedule.successful')],
    ];
    const grid = el('div', { class: 'request-summary-grid' });
    for (const [key, label] of metrics) {
      grid.appendChild(el('div', { class: `request-summary-card status-${key}` }, [
        el('span', { class: 'request-summary-value' }, String(counts[key] || 0)),
        el('span', { class: 'request-summary-label' }, label),
      ]));
    }
    grid.appendChild(el('div', { class: 'request-summary-card' }, [
      el('span', { class: 'request-summary-value' }, schedule.summary.longest_unresolved_chain_nodes == null ? '—' : String(schedule.summary.longest_unresolved_chain_nodes)),
      el('span', { class: 'request-summary-label' }, t('schedule.longestChain')),
    ]));
    grid.appendChild(el('div', { class: 'request-summary-card' }, [
      el('span', { class: 'request-summary-value request-policy-value' }, String(schedule.policy || 'fifo').toUpperCase()),
      el('span', { class: 'request-summary-label' }, t('schedule.policy')),
    ]));
    return grid;
  }

  function buildScheduleNode(node, index) {
    const decision = node.decision || {};
    const status = decision.status || 'waiting';
    const row = el('li', { class: `request-node status-${status}${node.cycle ? ' cycle-node' : ''}` });
    const wave = node.wave == null ? '—' : String(node.wave);
    const queue = node.queue_rank == null ? '—' : `#${node.queue_rank}`;
    const blockerIds = (node.blocked_by || []).map((blocker) => blocker.identifier);
    const header = el(node.exists ? 'button' : 'div', node.exists ? {
      type: 'button',
      class: 'request-node-main',
      onClick: () => openDrawer(node.identifier),
      'aria-label': t('schedule.openTicket', { id: node.identifier }),
    } : {
      class: 'request-node-main missing-node',
      'aria-label': t('schedule.missingTicket', { id: node.identifier }),
    }, [
      el('span', { class: 'request-node-order', 'aria-hidden': 'true' }, node.cycle ? '!' : String(index + 1)),
      el('span', { class: 'request-node-copy' }, [
        el('span', { class: 'request-node-id' }, node.identifier),
        el('span', { class: 'request-node-title' }, node.title || node.identifier),
      ]),
      el('span', { class: `schedule-status status-${status}` }, scheduleStatusLabel(status)),
    ]);
    row.appendChild(header);
    row.appendChild(el('div', { class: 'request-node-meta' }, [
      el('span', {}, t('schedule.stateValue', { value: node.state || '—' })),
      el('span', {}, t('schedule.queueValue', { value: queue })),
      el('span', {}, t('schedule.waveValue', { value: wave })),
      node.scope === 'external' ? el('span', { class: 'external-chip' }, t('schedule.external')) : null,
      node.cycle ? el('span', { class: 'cycle-chip' }, t('schedule.cycle')) : null,
    ].filter(Boolean)));
    row.appendChild(el('p', { class: 'request-node-reason' }, scheduleReasonLabel(decision)));
    const details = el('details', { class: 'request-node-details' });
    details.appendChild(el('summary', {}, t('schedule.details')));
    const list = el('dl', { class: 'schedule-details-list' });
    const values = [
      [t('schedule.blockedBy'), blockerIds.length ? blockerIds.join(', ') : t('schedule.none')],
      [t('schedule.unlocks'), (node.unlocks || []).length ? node.unlocks.join(', ') : t('schedule.none')],
      [t('schedule.globalCriticalPath'), node.global_critical_path_length == null ? '—' : String(node.global_critical_path_length + 1)],
      [t('schedule.decisionCode'), decision.code || '—'],
      [t('schedule.starvation'), node.starvation_promoted ? t('schedule.yes') : t('schedule.no')],
    ];
    for (const [term, value] of values) {
      list.appendChild(el('dt', {}, term));
      list.appendChild(el('dd', {}, value));
    }
    details.appendChild(list);
    row.appendChild(details);
    return row;
  }

  function renderRequestView(scrollEl) {
    if (!scrollEl) return;
    clearNode(scrollEl);
    const panel = el('section', {
      class: 'request-view',
      'aria-labelledby': 'request-view-title',
      'aria-busy': state.requestLoading ? 'true' : 'false',
    });
    panel.appendChild(el('div', { class: 'request-view-heading' }, [
      el('div', {}, [
        el('h2', { id: 'request-view-title' }, t('schedule.title')),
        el('p', { class: 'page-subtitle' }, t('schedule.subtitle')),
      ]),
      el('button', { class: 'btn btn-ghost', type: 'button', onClick: loadRequestCatalog }, t('common.refresh')),
    ]));
    if (state.requestLoading && !state.requestCatalog) {
      panel.appendChild(el('div', { role: 'status', class: 'request-loading-status' }, [
        buildSkeletonBlock(),
        el('span', { class: 'sr-only' }, t('schedule.loading')),
      ]));
      scrollEl.appendChild(panel);
      return;
    }
    const catalog = state.requestCatalog;
    if (!catalog) {
      panel.appendChild(el('div', { class: 'empty-state' }, t('schedule.chooseRequest')));
      scrollEl.appendChild(panel);
      return;
    }
    if (catalog.reason === 'unsupported_tracker' || catalog.reason === 'load_failed') {
      const message = catalog.reason === 'unsupported_tracker'
        ? t('schedule.unsupportedTracker')
        : t('schedule.unavailable', { error: catalog.error || catalog.reason || 'unknown' });
      panel.appendChild(el('div', { class: 'banner banner-info' }, message));
      scrollEl.appendChild(panel);
      return;
    }
    const rows = catalog.requests || [];
    if (!rows.length) {
      panel.appendChild(el('div', { class: 'empty-state' }, t('schedule.noRequests')));
      scrollEl.appendChild(panel);
      return;
    }
    const picker = el('select', {
      class: 'input request-picker',
      'aria-label': t('schedule.requestPicker'),
      onChange: (event) => {
        state.selectedRequestKey = event.target.value;
        state.requestSchedule = null;
        loadSelectedRequestSchedule();
      },
    });
    for (const row of rows) {
      picker.appendChild(el('option', {
        value: requestKey(row),
        selected: requestKey(row) === state.selectedRequestKey,
      }, row.kind === 'request'
        ? t('schedule.requestOption', { id: row.id, n: row.node_count })
        : t('schedule.ticketOption', { id: row.id })));
    }
    panel.appendChild(el('div', { class: 'request-picker-row' }, [
      el('label', {}, [el('span', { class: 'field-label' }, t('schedule.requestPicker')), picker]),
      el('span', { class: 'snapshot-time' }, catalog.generated_at ? t('schedule.generatedAt', { time: formatShortDateTime(catalog.generated_at) }) : t('schedule.notEvaluated')),
    ]));
    if (state.requestLoading || !state.requestSchedule) {
      panel.appendChild(el('div', { role: 'status', class: 'request-loading-status' }, [
        buildSkeletonBlock(),
        el('span', { class: 'sr-only' }, t('schedule.loading')),
      ]));
      scrollEl.appendChild(panel);
      return;
    }
    const schedule = state.requestSchedule;
    if (schedule.reason === 'load_failed') {
      panel.appendChild(el('div', { class: 'banner banner-info', role: 'alert' }, t('schedule.unavailable', { error: schedule.error || 'unknown' })));
      scrollEl.appendChild(panel);
      return;
    }
    if (schedule.stale) panel.appendChild(el('div', { class: 'banner banner-warning' }, t('schedule.stale')));
    if (schedule.decision_drifted) panel.appendChild(el('div', { class: 'banner banner-warning' }, t('schedule.decisionDrifted')));
    if ((schedule.warnings || []).includes('dependency_cycle')) panel.appendChild(el('div', { class: 'banner banner-warning', role: 'alert' }, t('schedule.cycleWarning')));
    if (!schedule.available) panel.appendChild(el('div', { class: 'banner banner-info' }, t('schedule.notEvaluated')));
    panel.appendChild(buildScheduleSummary(schedule));
    panel.appendChild(el('h3', { class: 'request-list-title' }, t('schedule.executionOrder')));
    panel.appendChild(el('p', { class: 'request-list-help' }, t('schedule.executionHelp')));
    const list = el(schedule.execution_valid === false ? 'ul' : 'ol', { class: 'request-node-list', 'aria-label': schedule.execution_valid === false ? t('schedule.invalidExecutionOrder') : t('schedule.executionOrder') });
    for (const [index, node] of (schedule.nodes || []).entries()) list.appendChild(buildScheduleNode(node, index));
    panel.appendChild(list);
    scrollEl.appendChild(panel);
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
    if (!board.read_only && !mobileSingleLane) grid.appendChild(el('div', { class: 'add-column-ghost', onClick: openAddColumnModal }, t('board.addColumnGhost')));
    layout.appendChild(grid);
    if (state.boardScope !== 'all') {
      const terminalGroups = columns
        .filter((col) => col.terminal)
        .map((col) => ({ col, issues: byColumn.get(col.name) || [] }))
        .filter((row) => row.issues.length > 0);
      if (terminalGroups.length) layout.appendChild(buildTerminalSectionEl(terminalGroups, live, board.read_only));
    }
    // First-run affordance: when every rendered lane is empty (and no
    // search filter is hiding cards), teach the two ways to add work.
    // Skipped while filtering — an empty result there is the filter's doing.
    if (!query) {
      const renderedCounts = columnsToRender.map((col) => (byColumn.get(col.name) || []).length);
      if (renderedCounts.length && renderedCounts.every((n) => n === 0)) {
        layout.appendChild(el('div', { class: 'board-empty-hint' },
          board.read_only ? t('board.emptyBoardReadonly') : t('board.emptyBoardHint')));
      }
    }
    scrollEl.appendChild(layout);
  }

  function buildTerminalSectionEl(groups, live, readOnly) {
    const total = groups.reduce((sum, row) => sum + row.issues.length, 0);
    const section = el('section', { class: 'terminal-section', 'aria-label': t('board.terminalStates') });
    section.appendChild(el('div', { class: 'terminal-section-header' }, [
      el('div', { class: 'terminal-section-title' }, t('workflow.reviewAndParked')),
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
      actions.push(el('button', { class: 'btn-icon', title: t('board.newIssue'), 'aria-label': t('board.newIssueInColumn', { name: col.name }), onClick: () => openIssueModal({ state: col.name }) }, '+'));
      actions.push(el('button', { class: 'btn-icon', title: t('board.columnMenu'), 'aria-label': t('board.columnMenuAria', { name: col.name }), onClick: (e) => { e.stopPropagation(); openColumnMenu(col, e.currentTarget); } }, '⋯'));
    }
    const header = el('div', { class: 'column-header' }, [
      el('div', { class: 'column-title-wrap' }, [dot, el('span', { class: 'column-title' }, col.name), el('span', { class: 'column-count' }, String(issues.length))]),
      el('div', { class: 'column-actions' }, actions),
    ]);
    const body = el('div', { class: 'column-body' });
    if (!issues.length) body.appendChild(el('div', { class: 'column-empty' }, t('board.noTickets')));
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
      showToast(t('board.moveFailed', { id: identifier, error: err.message }), 'error');
    });
  }

  function buildAttentionBadge(attention) {
    if (!attention) return null;
    return el('span', {
      class: `chip-attention attention-${attention.kind || 'info'}`,
      title: attention.message || attention.label || t('board.attentionRequired'),
    }, attention.label || t('board.attention'));
  }

  function blockedByIds(issue) {
    if (!issue || !Array.isArray(issue.blocked_by)) return [];
    return issue.blocked_by
      .map((b) => (typeof b === 'string' ? b : (b && b.identifier) || ''))
      .filter(Boolean);
  }

  function parseIdList(value) {
    return String(value || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function buildCardEl(issue, liveEntry, readOnly) {
    const card = el('div', {
      class: `card${liveEntry && liveEntry.paused ? ' paused' : ''}`,
      draggable: !readOnly,
      // Keyboard path to the drawer: the card is a real tab stop. The
      // whole card stays a div (it nests the skip/recover buttons, which
      // cannot live inside a <button>), so role+key handling stand in.
      tabindex: '0',
      role: 'button',
      'aria-label': t('board.openTicketAria', { id: issue.identifier, title: issue.title }),
      onClick: () => openDrawer(issue.identifier),
      onKeydown: (e) => {
        if (boardIsStale()) return;
        if (e.target !== card || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        openDrawer(issue.identifier);
      },
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
    else if (issue.last_agent_kind) {
      // Stage-routed board: no pin is written, so show who actually ran it.
      badges.appendChild(el('span', {
        class: 'chip-agent chip-agent-last',
        title: t('board.lastAgentTitle'),
      }, issue.last_agent_kind));
    }
    // The API has always returned `blocked_by` and `request`; without these
    // chips the DAG the chat intake files is invisible on the board it points
    // the operator at.
    const blockerIds = blockedByIds(issue);
    if (blockerIds.length) {
      badges.appendChild(el('span', {
        class: 'chip-blocked',
        title: t('board.blockedByTitle', { ids: blockerIds.join(', ') }),
      }, `⛓ ${blockerIds.join(', ')}`));
    }
    if (issue.request) badges.appendChild(el('span', { class: 'chip-request' }, issue.request));
    const attentionBadge = buildAttentionBadge(issue.attention);
    if (attentionBadge) badges.appendChild(attentionBadge);
    if (badges.childNodes.length) card.appendChild(badges);
    if (!readOnly && isDocumentState(issue.state) && !liveEntry) {
      card.appendChild(el('button', {
        class: 'btn btn-ghost btn-sm card-action',
        onClick: async (e) => {
          e.stopPropagation();
          await runControlAction(api.skipDocument, issue.identifier, t('issue.skippedDocument'));
        },
      }, t('issue.skipDocument')));
    }
    if (!readOnly && isBlockedState(issue.state) && !liveEntry) {
      card.appendChild(el('button', {
        class: 'btn btn-ghost btn-sm card-action',
        onClick: async (e) => {
          e.stopPropagation();
          await runControlAction(api.recoverBlocked, issue.identifier, t('issue.rcaQueued'));
        },
      }, t('issue.openRca')));
    }
    if (liveEntry) card.appendChild(buildLiveRow(liveEntry));
    return card;
  }

  function buildLiveRow(liveEntry) {
    const statusLine = el('div', { class: 'live-status-line' });
    if (liveEntry.status === 'retrying') {
      statusLine.appendChild(el('span', { class: 'live-icon retry' }, '↻'));
      // Say why it is retrying — the bare ↻ hid the error the API sends.
      statusLine.appendChild(el('span', null,
        liveEntry.attempt != null
          ? t('issue.retryingAttempt', { n: liveEntry.attempt })
          : t('common.retrying')));
      if (liveEntry.error) {
        statusLine.appendChild(el('span', {
          class: 'live-error',
          title: liveEntry.error,
        }, truncate(liveEntry.error, 80)));
      }
    } else {
      statusLine.appendChild(el('span', { class: 'live-dot' }));
      statusLine.appendChild(el('span', null, t('issue.turnCount', { n: liveEntry.turn_count ?? 0 })));
    }
    const totalTokens = liveEntry.tokens && liveEntry.tokens.total_tokens;
    if (totalTokens != null) statusLine.appendChild(el('span', null, t('issue.tokensShort', { n: formatCompactNumber(totalTokens) })));
    if (liveEntry.paused) statusLine.appendChild(el('span', { class: 'badge-paused' }, t('common.pausedBadge')));
    const row = el('div', { class: 'card-live' }, [statusLine]);
    if (liveEntry.last_message) row.appendChild(el('div', { class: 'live-message' }, truncate(liveEntry.last_message, 80)));
    return row;
  }

  function openIssueModal(defaults = {}) {
    const titleInput = el('input', { class: 'input', type: 'text', placeholder: t('board.issueTitle'), required: true });
    const descInput = el('textarea', { class: 'textarea', rows: 4, placeholder: t('board.descriptionOptional') });
    const stateSelect = buildStateSelect(defaults.state);
    const prioritySelect = buildPrioritySelect(null);
    const labelsInput = el('input', { class: 'input', type: 'text', placeholder: t('board.labelsPlaceholder') });
    const agentSelect = buildAgentSelect('');
    const prefixInput = el('input', { class: 'input', type: 'text', placeholder: 'TASK', maxlength: 16 });
    const blockedByInput = el('input', { class: 'input', type: 'text', placeholder: t('board.blockedByPlaceholder') });
    const requestInput = el('input', { class: 'input', type: 'text', placeholder: t('board.requestPlaceholder') });

    const body = el('div', { class: 'form-stack' }, [
      field(t('common.title'), titleInput),
      field(t('common.description'), descInput),
      fieldRow([field(t('common.state'), stateSelect), field(t('common.priority'), prioritySelect)]),
      field(t('common.labels'), labelsInput),
      fieldRow([field(t('common.blockedBy'), blockedByInput), field(t('common.request'), requestInput)]),
      fieldRow([field(t('common.agent'), agentSelect), field(t('settings.idPrefix'), prefixInput)]),
    ]);

    openFormModal({
      title: t('board.newIssue'),
      submitLabel: t('board.createIssue'),
      body,
      onSubmit: async () => {
        const title = titleInput.value.trim();
        if (!title) throw new Error(t('board.titleRequired'));
        const created = await api.createIssue({
          title,
          description: descInput.value,
          state: stateSelect.value,
          priority: prioritySelect.value === '' ? null : Number(prioritySelect.value),
          labels: parseLabels(labelsInput.value),
          agent_kind: agentSelect.value,
          blocked_by: parseIdList(blockedByInput.value),
          request: requestInput.value.trim(),
          prefix: prefixInput.value.trim() || 'TASK',
        });
        showToast(t('board.issueCreated', { id: created.identifier }), 'success');
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
      drawer.appendChild(el('div', { class: 'drawer-error' }, t('issue.loadFailed', { id: identifier, error: err.message })));
    }
  }

  async function commitField(identifier, fieldName, value, onError, onSuccess) {
    try {
      await api.patchIssue(identifier, { [fieldName]: value });
      showToast(t('common.saved'), 'success');
      if (onSuccess) onSuccess();
      await refreshBoard();
    } catch (err) {
      showToast(t('issue.saveFailed', { field: fieldName, error: err.message }), 'error');
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
      el('button', { class: 'btn-icon', 'aria-label': t('common.close'), onClick: closeDrawer }, '✕'),
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

    const blockedByInput = el('input', {
      class: 'input',
      type: 'text',
      value: blockedByIds(detail).join(', '),
      placeholder: t('board.blockedByPlaceholder'),
    });
    const commitBlockedBy = () => {
      const ids = parseIdList(blockedByInput.value);
      const current = blockedByIds(detail);
      if (JSON.stringify(ids) === JSON.stringify(current)) return;
      commitField(
        detail.identifier, 'blocked_by', ids,
        () => { blockedByInput.value = current.join(', '); },
        () => { detail.blocked_by = ids.map((identifier) => ({ identifier, state: null })); },
      );
    };
    blockedByInput.addEventListener('blur', commitBlockedBy);
    blockedByInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') blockedByInput.blur(); });

    const requestInput = el('input', {
      class: 'input',
      type: 'text',
      value: detail.request || '',
      placeholder: t('board.requestPlaceholder'),
    });
    const commitRequest = () => {
      const value = requestInput.value.trim();
      if (value === (detail.request || '')) return;
      commitField(
        detail.identifier, 'request', value,
        () => { requestInput.value = detail.request || ''; },
        () => { detail.request = value; },
      );
    };
    requestInput.addEventListener('blur', commitRequest);
    requestInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') requestInput.blur(); });

    const fieldsGrid = el('div', { class: 'drawer-fields' }, [
      field(t('common.state'), stateSelect),
      field(t('common.priority'), prioritySelect),
      field(t('common.agent'), agentSelect),
      field(t('common.labels'), labelsInput),
      field(t('common.blockedBy'), blockedByInput),
      field(t('common.request'), requestInput),
    ]);

    const deleteBtn = el('button', {
      class: 'btn btn-danger-outline',
      onClick: async () => {
        const ok = await confirmDialog(t('issue.deleteConfirm', { id: detail.identifier }));
        if (!ok) return;
        try {
          await api.deleteIssue(detail.identifier);
          showToast(t('issue.deleted', { id: detail.identifier }), 'success');
          closeDrawer();
          await refreshBoard();
        } catch (err) {
          showToast(err.message, 'error');
        }
      },
    }, t('issue.deleteIssue'));

    container.appendChild(header);
    container.appendChild(titleInput);
    container.appendChild(fieldsGrid);
    if (detail.attention) {
      container.appendChild(el('div', { class: `drawer-attention attention-${detail.attention.kind || 'info'}` }, [
        el('strong', null, detail.attention.label || t('board.attention')),
        el('span', null, detail.attention.message || ''),
      ]));
    }
    if (!detail.live && isDocumentState(detail.state)) {
      container.appendChild(el('button', {
        class: 'btn btn-ghost',
        onClick: async () => {
          await runControlAction(api.skipDocument, detail.identifier, t('issue.skippedDocument'));
        },
      }, t('issue.skipDocument')));
    }
    if (!detail.live && isBlockedState(detail.state)) {
      container.appendChild(el('button', {
        class: 'btn btn-ghost',
        onClick: async () => {
          await runControlAction(api.recoverBlocked, detail.identifier, t('issue.rcaQueued'));
        },
      }, t('issue.openRca')));
    }
    if (detail.live) container.appendChild(buildLiveSection(detail));
    container.appendChild(buildRunHistorySection(detail));
    container.appendChild(buildArtifactsSection(detail));
    container.appendChild(buildDescriptionSection(detail));
    container.appendChild(el('div', { class: 'drawer-meta' }, [
      el('div', null, t('issue.createdAgo', { ago: timeAgo(detail.created_at) })),
      el('div', null, t('issue.updatedAgo', { ago: timeAgo(detail.updated_at) })),
    ]));
    container.appendChild(deleteBtn);
    return container;
  }

  function buildRunHistorySection(detail) {
    const rows = el('div', { class: 'run-history-rows' }, [
      el('div', { class: 'history-muted' }, t('issue.loadingRunHistory')),
    ]);
    const section = el('div', { class: 'drawer-run-history' }, [
      el('div', { class: 'section-heading' }, t('issue.runHistory')),
      rows,
    ]);
    loadRunHistory(detail.identifier, rows);
    return section;
  }

  async function loadRunHistory(identifier, rows) {
    try {
      const data = await api.getRuns({ issue: identifier, limit: 10 });
      if (state.drawerIssue !== identifier) return;
      clearNode(rows);
      if (data.registry_error) {
        rows.appendChild(el('div', { class: 'history-muted' }, t('issue.historyUnavailable')));
        return;
      }
      const runs = data.runs || [];
      if (!runs.length) {
        rows.appendChild(el('div', { class: 'history-muted' }, t('issue.noRunsRecorded')));
        return;
      }
      for (const run of runs) rows.appendChild(buildRunHistoryRow(run));
    } catch (_err) {
      if (state.drawerIssue !== identifier) return;
      clearNode(rows);
      rows.appendChild(el('div', { class: 'history-muted' }, t('issue.historyUnavailable')));
    }
  }

  function buildRunHistoryRow(run) {
    const attempt = run.attempt_kind || 'run';
    const agent = run.agent_kind || 'agent';
    const status = run.status || t('common.unknown');
    const start = formatShortDateTime(run.started_at);
    const end = run.completed_at ? formatShortDateTime(run.completed_at) : t('common.openEnded');
    return el('button', {
      class: 'run-history-row',
      type: 'button',
      title: t('runs.openExplorer'),
      onClick: () => {
        state.selectedRunId = run.run_id;
        navigate('runs');
      },
    }, [
      el('span', { class: 'run-history-main' }, `${attempt} ${agent}`),
      el('span', { class: 'run-history-status' }, status),
      el('span', { class: 'run-history-time' }, `${start} -> ${end}`),
    ]);
  }

  function formatArtifactBytes(size) {
    const bytes = Number(size) || 0;
    if (bytes < 1024) return `${bytes} B`;
    let value = bytes;
    for (const unit of ['KB', 'MB', 'GB']) {
      value /= 1024;
      if (value < 1024 || unit === 'GB') {
        return `${value.toFixed(1).replace(/\.0$/, '')} ${unit}`;
      }
    }
    return `${bytes} B`;
  }

  // Worker deliverables collected off the workspace. Images preview inline;
  // everything else is a download link — the server already forces
  // `Content-Disposition: attachment` for types a browser could execute.
  function buildArtifactsSection(detail) {
    const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts : [];
    const section = el('div', { class: 'drawer-artifacts' });
    section.appendChild(el('div', { class: 'section-heading' }, [
      el('span', null, `${t('artifacts.heading')} (${artifacts.length})`),
    ]));
    if (!artifacts.length) {
      section.appendChild(el('div', { class: 'form-hint' }, t('artifacts.empty')));
      section.appendChild(el('div', { class: 'form-hint' }, t('artifacts.hint')));
      return section;
    }
    const list = el('div', { class: 'artifact-list' });
    artifacts.forEach((artifact) => {
      const isImage = artifact.inline && String(artifact.content_type || '').startsWith('image/');
      // Always show the real file name next to the worker-chosen title: the
      // title is arbitrary text, so "Coverage report" could otherwise save
      // an installer without the reader ever seeing the extension.
      const meta = [artifact.name, formatArtifactBytes(artifact.byte_size)];
      if (artifact.turn) meta.push(t('artifacts.turn', { turn: artifact.turn }));
      const link = el('a', {
        class: 'artifact-name',
        href: artifact.url,
        target: '_blank',
        rel: 'noopener noreferrer',
        download: artifact.inline ? null : artifact.name,
      }, artifact.title || artifact.name);
      const item = el('div', { class: 'artifact-item' }, [
        el('div', { class: 'artifact-row' }, [
          link,
          el('span', { class: 'artifact-meta' }, meta.join(' · ')),
        ]),
      ]);
      if (artifact.summary) {
        item.appendChild(el('div', { class: 'artifact-summary' }, artifact.summary));
      }
      if (isImage) {
        const thumb = el('img', {
          class: 'artifact-thumb',
          src: artifact.url,
          alt: artifact.title || artifact.name,
          loading: 'lazy',
        });
        const preview = el('a', {
          href: artifact.url,
          target: '_blank',
          rel: 'noopener noreferrer',
          class: 'artifact-thumb-link',
        }, [thumb]);
        item.appendChild(preview);
      }
      list.appendChild(item);
    });
    section.appendChild(list);
    return section;
  }

  function buildDescriptionSection(detail) {
    let editing = false;
    const section = el('div', { class: 'drawer-description' });
    const editBtn = el('button', { class: 'btn btn-ghost btn-sm', onClick: toggle }, t('common.edit'));
    const heading = el('div', { class: 'section-heading' }, [el('span', null, t('common.description')), editBtn]);
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
              editBtn.textContent = t('common.edit');
              showToast(t('issue.descriptionSaved'), 'success');
              renderView();
            } catch (err) {
              errorBox.textContent = err.message;
              errorBox.style.display = 'block';
            }
          },
        }, t('common.save'));
        body.appendChild(textarea);
        body.appendChild(errorBox);
        body.appendChild(el('div', { class: 'description-actions' }, [saveBtn]));
      } else if (detail.description) {
        body.appendChild(renderMarkdown(detail.description));
      } else {
        body.appendChild(el('div', { class: 'form-hint' }, t('board.noDescription')));
      }
    }

    function toggle() {
      editing = !editing;
      editBtn.textContent = editing ? t('common.cancel') : t('common.edit');
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
      liveStat(t('common.status'), live.status || t('common.unknown')),
      liveStat(t('common.turn'), String(live.turn_count ?? 0)),
      liveStat(t('stats.tokensIn'), formatCompactNumber(tokens.input_tokens ?? 0)),
      liveStat(t('stats.tokensOut'), formatCompactNumber(tokens.output_tokens ?? 0)),
      liveStat(t('stats.tokensTotal'), formatCompactNumber(tokens.total_tokens ?? 0)),
      liveStat(t('issue.lastEvent'), live.last_event || '—'),
    ]);
    const section = el('div', { class: 'drawer-live' }, [el('div', { class: 'section-heading' }, t('issue.liveRun')), grid]);
    if (live.last_message) section.appendChild(el('div', { class: 'live-message-block' }, live.last_message));
    const runControl = live.paused
      ? el('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { await runControlAction(api.resume, detail.identifier, t('issue.resumed')); } }, t('issue.resume'))
      : el('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { await runControlAction(api.pause, detail.identifier, t('common.paused')); } }, t('issue.pause'));
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
      state.lastSuccessfulPollAt = Date.now();
      updateConnectionIndicator();
      if (state.route === 'board') renderBoardSurface(document.getElementById('board-scroll'));
    } catch (_err) {
      // regular poll loop will surface connectivity issues
    }
  }


  // ------------------------------------------------------------------
  // Page: Runs
  // ------------------------------------------------------------------

  let runsPollTimer = null;

  function cancelRunsPoll() {
    if (runsPollTimer != null) clearTimeout(runsPollTimer);
    runsPollTimer = null;
  }

  function scheduleRunsPoll(page) {
    cancelRunsPoll();
    runsPollTimer = setTimeout(async () => {
      if (state.route !== 'runs' || !document.body.contains(page)) return;
      await loadRunsPage(page, { quiet: true });
      if (state.route === 'runs' && document.body.contains(page)) scheduleRunsPoll(page);
    }, 5000);
  }

  function renderRunsPage(container) {
    const page = el('div', { class: 'page page-runs' });
    page._runs = [];
    page._detail = null;

    const search = el('input', {
      id: 'runs-search',
      class: 'input runs-search',
      type: 'search',
      'aria-label': t('runs.searchAria'),
      placeholder: t('runs.searchPlaceholder'),
      onInput: () => applyRunFilters(page, { debounce: true }),
    });
    const statusFilter = el('select', {
      id: 'runs-status-filter',
      class: 'select',
      onChange: () => applyRunFilters(page),
    }, [el('option', { value: '' }, t('runs.allStatuses'))]);
    const agentFilter = el('select', {
      id: 'runs-agent-filter',
      class: 'select',
      onChange: () => applyRunFilters(page),
    }, [el('option', { value: '' }, t('runs.allAgents'))]);
    const refresh = el('button', {
      class: 'btn btn-ghost',
      onClick: () => loadRunsPage(page),
    }, t('common.refresh'));

    page._runsControls = { search, statusFilter, agentFilter };
    page.appendChild(el('div', { class: 'topbar runs-topbar' }, [
      el('div', { class: 'topbar-left' }, [
        el('h1', { class: 'page-title' }, t('nav.runs')),
        el('span', { class: 'page-subtitle' }, t('runs.subtitle')),
      ]),
      el('div', { class: 'topbar-right runs-filters' }, [search, statusFilter, agentFilter, refresh]),
    ]));

    const list = el('div', { class: 'run-attempt-list', id: 'run-attempt-list' }, [buildSkeletonBlock()]);
    const detail = el('div', { class: 'run-attempt-detail', id: 'run-attempt-detail' }, [
      el('div', { class: 'empty-state' }, t('runs.selectAttempt')),
    ]);
    page.appendChild(el('div', { class: 'runs-layout' }, [list, detail]));
    container.appendChild(page);
    loadRunsPage(page).finally(() => {
      if (state.route === 'runs' && document.body.contains(page)) scheduleRunsPoll(page);
    });
  }

  function applyRunFilters(page, { debounce = false } = {}) {
    if (page._runsSearchTimer) clearTimeout(page._runsSearchTimer);
    state.selectedRunId = null;
    const refresh = () => loadRunsPage(page);
    if (debounce) page._runsSearchTimer = setTimeout(refresh, 250);
    else refresh();
  }

  async function loadRunsPage(page, { quiet = false } = {}) {
    try {
      const { search, statusFilter, agentFilter } = page._runsControls;
      const data = await api.getRuns({
        limit: 200,
        query: search.value.trim() || undefined,
        status: statusFilter.value || undefined,
        agent: agentFilter.value || undefined,
      });
      if (state.route !== 'runs' || !document.body.contains(page)) return;
      page._runs = data.runs || [];
      populateRunFilters(page);
      renderRunAttemptList(page);
      const selected = state.selectedRunId || (page._runs[0] && page._runs[0].run_id);
      if (selected) {
        state.selectedRunId = selected;
        await loadRunDetail(page, selected, { quiet });
      } else {
        renderRunDetail(page, null);
      }
    } catch (err) {
      if (quiet || state.route !== 'runs' || !document.body.contains(page)) return;
      const list = page.querySelector('#run-attempt-list');
      clearNode(list);
      list.appendChild(el('div', { class: 'empty-state' }, t('runs.loadFailed', { error: err.message })));
    }
  }

  function populateRunFilters(page) {
    const { statusFilter, agentFilter } = page._runsControls;
    const currentStatus = statusFilter.value;
    const currentAgent = agentFilter.value;
    const statuses = [...new Set(page._runs.map((run) => run.status).filter(Boolean))].sort();
    const agents = [...new Set(page._runs.map((run) => run.agent_kind).filter(Boolean))].sort();
    clearNode(statusFilter);
    statusFilter.appendChild(el('option', { value: '' }, t('runs.allStatuses')));
    for (const status of statuses) statusFilter.appendChild(el('option', { value: status }, status));
    clearNode(agentFilter);
    agentFilter.appendChild(el('option', { value: '' }, t('runs.allAgents')));
    for (const agent of agents) agentFilter.appendChild(el('option', { value: agent }, agent));
    statusFilter.value = statuses.includes(currentStatus) ? currentStatus : '';
    agentFilter.value = agents.includes(currentAgent) ? currentAgent : '';
  }

  function filteredRuns(page) {
    const { search, statusFilter, agentFilter } = page._runsControls;
    const query = search.value.trim().toLowerCase();
    return page._runs.filter((run) => {
      if (statusFilter.value && run.status !== statusFilter.value) return false;
      if (agentFilter.value && run.agent_kind !== agentFilter.value) return false;
      if (!query) return true;
      return [
        run.identifier,
        run.title,
        run.status,
        run.agent_kind,
        run.attempt_kind,
        run.failure_class,
        run.error_class,
      ].some((value) => String(value || '').toLowerCase().includes(query));
    });
  }

  function renderRunAttemptList(page) {
    const list = page.querySelector('#run-attempt-list');
    if (!list) return;
    clearNode(list);
    const runs = filteredRuns(page);
    if (!runs.length) {
      list.appendChild(el('div', { class: 'empty-state' }, t('runs.noAttempts')));
      return;
    }
    for (const run of runs) list.appendChild(buildRunAttemptRow(page, run));
  }

  function buildRunAttemptRow(page, run) {
    const selected = state.selectedRunId === run.run_id;
    const totalTokens = run.tokens ? run.tokens.total : run.total_tokens;
    const tokenTotal = totalTokens == null ? null : formatCompactNumber(totalTokens);
    const title = run.title || run.identifier || run.run_id;
    return el('button', {
      class: `run-attempt-row${selected ? ' selected' : ''}`,
      type: 'button',
      onClick: async () => {
        state.selectedRunId = run.run_id;
        renderRunAttemptList(page);
        await loadRunDetail(page, run.run_id);
      },
    }, [
      el('div', { class: 'run-attempt-heading' }, [
        el('strong', null, run.identifier || run.run_id),
        el('span', { class: `run-status run-status-${runStatusTone(run.status)}` }, run.status || t('common.unknown')),
      ]),
      el('div', { class: 'run-attempt-title' }, title),
      el('div', { class: 'run-attempt-meta' }, [
        el('span', null, `${run.attempt_kind || 'run'} · ${run.agent_kind || t('common.unknown')}`),
        el('span', null, formatShortDateTime(run.started_at)),
        tokenTotal != null ? el('span', null, t('runs.tokenCount', { count: tokenTotal })) : null,
      ]),
    ]);
  }

  function runStatusTone(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'active' || value === 'reclaiming') return 'active';
    if (value === 'normal' || value === 'completed' || value === 'done') return 'ok';
    if (value === 'cancelled' || value === 'paused' || value === 'manual_stop') return 'neutral';
    return value ? 'failed' : 'neutral';
  }

  function runEventLabel(eventType) {
    if (!eventType) return t('common.unknown');
    return t(`runs.event.${eventType}`);
  }

  async function loadRunDetail(page, runId, { quiet = false } = {}) {
    try {
      const detail = await api.getRunDetail(runId);
      if (state.route !== 'runs' || !document.body.contains(page) || state.selectedRunId !== runId) return;
      page._detail = detail;
      if (detail.run && !page._runs.some((run) => run.run_id === detail.run.run_id)) {
        page._runs.unshift(detail.run);
        populateRunFilters(page);
      }
      renderRunDetail(page, detail);
      renderRunAttemptList(page);
    } catch (err) {
      if (quiet || state.route !== 'runs' || !document.body.contains(page)) return;
      const target = page.querySelector('#run-attempt-detail');
      clearNode(target);
      target.appendChild(el('div', { class: 'empty-state' }, t('runs.detailFailed', { error: err.message })));
    }
  }

  function renderRunDetail(page, detail) {
    const target = page.querySelector('#run-attempt-detail');
    if (!target) return;
    clearNode(target);
    if (!detail || !detail.run) {
      target.appendChild(el('div', { class: 'empty-state' }, t('runs.selectAttempt')));
      return;
    }
    const run = detail.run;
    const download = el('button', {
      class: 'btn btn-ghost',
      onClick: async () => {
        try {
          await api.downloadRunDiagnostic(run.run_id);
          showToast(t('runs.diagnosticDownloaded'), 'success');
        } catch (err) {
          showToast(err.message, 'error');
        }
      },
    }, t('runs.downloadDiagnostic'));
    target.appendChild(el('div', { class: 'run-detail-header' }, [
      el('div', null, [
        el('div', { class: 'eyebrow' }, `${run.attempt_kind || 'run'} / ${run.agent_kind || t('common.unknown')}`),
        el('h2', null, `${run.identifier || run.run_id}: ${run.title || ''}`),
      ]),
      download,
    ]));
    target.appendChild(el('div', { class: 'run-summary-grid' }, [
      runSummaryValue(t('common.status'), run.status || t('common.unknown')),
      runSummaryValue(t('common.state'), run.state || '—'),
      runSummaryValue(t('runs.duration'), runDuration(run)),
      runSummaryValue(t('common.tokens'), (run.tokens ? run.tokens.total : run.total_tokens) == null ? '—' : formatCompactNumber(run.tokens ? run.tokens.total : run.total_tokens)),
      runSummaryValue(t('runs.failureClass'), run.failure_class || run.error_class || '—'),
    ]));
    target.appendChild(buildRunMetadata(page, run));
    if (run.failure_message || run.error_message_redacted) {
      target.appendChild(el('div', { class: 'run-error-block' }, [
        el('strong', null, run.failure_class || run.error_class || t('common.failed')),
        el('pre', null, run.failure_message || run.error_message_redacted),
      ]));
    }
    target.appendChild(el('div', { class: 'section-heading' }, t('runs.timeline')));
    target.appendChild(buildRunTimeline(detail.events || []));
  }

  function runSummaryValue(label, value) {
    return el('div', { class: 'run-summary-value' }, [
      el('span', null, label),
      el('strong', null, value),
    ]);
  }

  function runDuration(run) {
    if (!run.started_at) return '—';
    const start = Date.parse(run.started_at);
    const end = run.completed_at ? Date.parse(run.completed_at) : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—';
    return humanizeSeconds((end - start) / 1000);
  }

  function buildRunMetadata(page, run) {
    const rows = [
      [t('runs.started'), formatShortDateTime(run.started_at)],
      [t('runs.completed'), run.completed_at ? formatShortDateTime(run.completed_at) : t('common.openEnded')],
      [t('runs.workspace'), run.workspace_path || '—'],
      [t('common.branch'), run.branch_name || '—'],
      [t('runs.commit'), run.commit_sha || '—'],
      [t('runs.tokensIn'), (run.tokens ? run.tokens.input : run.input_tokens) == null ? '—' : formatCompactNumber(run.tokens ? run.tokens.input : run.input_tokens)],
      [t('runs.tokensCache'), (run.tokens ? run.tokens.cache : run.cache_input_tokens) == null ? '—' : formatCompactNumber(run.tokens ? run.tokens.cache : run.cache_input_tokens)],
      [t('runs.tokensOut'), (run.tokens ? run.tokens.output : run.output_tokens) == null ? '—' : formatCompactNumber(run.tokens ? run.tokens.output : run.output_tokens)],
    ];
    if (run.continued_from_run_id) {
      rows.push([t('runs.continuedFrom'), el('button', {
        class: 'run-metadata-link',
        type: 'button',
        title: run.continued_from_run_id,
        onClick: async () => {
          state.selectedRunId = run.continued_from_run_id;
          renderRunAttemptList(page);
          await loadRunDetail(page, run.continued_from_run_id);
        },
      }, run.continued_from_run_id)]);
    }
    if (run.checkpoint && run.checkpoint.state && run.checkpoint.turn != null) {
      rows.push([t('runs.checkpoint'), t('runs.checkpointValue', {
        state: run.checkpoint.state,
        turn: run.checkpoint.turn,
        time: formatShortDateTime(run.checkpoint.checkpointed_at),
      })]);
    }
    return el('dl', { class: 'run-metadata' }, rows.flatMap(([label, value]) => {
      const isNode = value && typeof value === 'object' && value.nodeType;
      return [
        el('dt', null, label),
        el('dd', isNode ? null : { title: String(value) }, isNode ? value : String(value)),
      ];
    }));
  }

  function buildRunTimeline(events) {
    if (!events.length) return el('div', { class: 'empty-state run-timeline-empty' }, t('runs.noEvents'));
    return el('ol', { class: 'run-timeline' }, events.map((event) => {
      const payload = event.payload && typeof event.payload === 'object' ? event.payload : {};
      const details = Object.entries(payload)
        .filter(([, value]) => value != null && value !== '')
        .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`)
        .join(' · ');
      return el('li', { class: `run-timeline-event severity-${event.severity || 'info'}` }, [
        el('div', { class: 'run-timeline-marker', 'aria-hidden': 'true' }),
        el('div', { class: 'run-timeline-content' }, [
          el('div', { class: 'run-timeline-heading' }, [
            el('strong', null, runEventLabel(event.event_type || event.type)),
            el('time', null, formatShortDateTime(event.created_at)),
          ]),
          event.message ? el('div', { class: 'run-timeline-message' }, event.message) : null,
          details ? el('div', { class: 'run-timeline-payload' }, details) : null,
        ]),
      ]);
    }));
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
    page.appendChild(el('div', { class: 'topbar' }, [el('div', { class: 'topbar-left' }, [el('h1', { class: 'page-title' }, t('nav.stats'))]), el('div', { class: 'topbar-right' }, [picker])]));
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
        content.appendChild(el('div', { class: 'empty-state' }, t('stats.loadFailed', { error: err.message })));
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
    if (!points.length) return el('div', { class: 'chart-empty' }, t('common.noData'));
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
    if (!points.length) return el('div', { class: 'chart-empty' }, t('common.noData'));
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
    if (!rows.length) return el('div', { class: 'chart-empty' }, t('stats.noAgentActivity'));
    const tbody = el('tbody', null, rows.map((row) => el('tr', null, [
      el('td', null, row.agent),
      el('td', null, formatCompactNumber(row.total_tokens)),
      el('td', null, String(row.turns)),
      el('td', null, String(row.runs)),
    ])));
    return el('table', { class: 'data-table' }, [
      el('thead', null, el('tr', null, [t('common.agent'), t('common.tokens'), t('common.turns'), t('stats.runs')].map((h) => el('th', null, h)))),
      tbody,
    ]);
  }

  function renderStatsContent(data) {
    const content = document.getElementById('stats-content');
    clearNode(content);
    const hasEvents = data.totals.turns > 0 || data.totals.runs > 0 || data.by_day.length > 0;
    if (!hasEvents) {
      content.appendChild(el('div', { class: 'empty-state' }, t('stats.noActivity')));
      return;
    }
    content.appendChild(el('div', { class: 'stat-grid' }, [
      statTile(t('stats.ticketsDone'), String(data.totals.done)),
      statTile(t('stats.totalTokens'), formatCompactNumber(data.totals.total)),
      statTile(t('common.turns'), String(data.totals.turns)),
      statTile(t('stats.runs'), String(data.totals.runs)),
      statTile(t('stats.avgCycleTime'), data.cycle.avg_seconds ? humanizeSeconds(data.cycle.avg_seconds) : '—'),
      statTile(t('issue.liveRunning'), String(data.live.running)),
    ]));

    const chartsGrid = el('div', { class: 'charts-grid' });
    chartsGrid.appendChild(chartCard(t('stats.tokensPerDay'), barChart(data.by_day.map((d) => ({ label: d.date.slice(5), value: d.total })))));
    chartsGrid.appendChild(chartCard(t('stats.donePerDay'), barChart(data.by_day.map((d) => ({ label: d.date.slice(5), value: d.done })))));
    chartsGrid.appendChild(chartCard(t('stats.tokensByColumn'), hBarChart(mapStateLabels(data.by_state, (s) => s.total_tokens))));
    chartsGrid.appendChild(chartCard(t('stats.avgTimeInColumn'), hBarChart(mapStateLabels(data.by_state, (s) => s.avg_dwell_seconds), { formatValue: humanizeSeconds })));
    content.appendChild(chartsGrid);
    content.appendChild(chartCard(t('stats.byAgent'), buildAgentTable(data.by_agent)));
  }

  // ------------------------------------------------------------------
  // Page: Workflow
  // ------------------------------------------------------------------

  async function renderWorkflowPage(container) {
    const page = el('div', { class: 'page page-workflow' });
    page.appendChild(el('div', { class: 'topbar' }, [el('h1', { class: 'page-title' }, t('nav.workflow'))]));
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
      body.appendChild(el('div', { class: 'empty-state' }, t('workflow.loadFailed', { error: err.message })));
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
    }, t('board.addColumnGhost')));
    const saveBar = el('div', { class: 'save-bar', id: 'wf-save-bar', style: 'display:none;' }, [
      el('span', null, t('workflow.unsavedChanges')),
      el('div', { class: 'save-bar-actions' }, [
        el('button', {
          class: 'btn btn-ghost',
          onClick: () => {
            state.workflowDraft = state.workflow.columns.map((c) => ({ ...c, _originalName: c.name }));
            state.wfRerender();
          },
        }, t('common.discard')),
        el('button', { class: 'btn btn-primary', onClick: saveWorkflowChanges }, t('common.saveChanges')),
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
    const descInput = el('input', { class: 'input wf-desc', type: 'text', value: row.description, placeholder: t('common.description'), oninput: (e) => { row.description = e.target.value; updateSaveBarVisibility(); } });
    const terminalInput = el('input', { type: 'checkbox', checked: row.terminal, onChange: (e) => { row.terminal = e.target.checked; state.wfRerender(); } });
    const terminalToggle = el('label', { class: 'switch' }, [terminalInput, el('span', { class: 'switch-slider' })]);

    const rowChildren = [
      el('span', { class: 'drag-handle', 'aria-hidden': 'true' }, '⋮⋮'),
      nameInput,
      descInput,
      el('div', { class: 'wf-terminal-field' }, [terminalToggle, el('span', { class: 'wf-terminal-label' }, t('common.terminal'))]),
    ];
    if (row.has_prompt && !row.terminal) {
      rowChildren.push(el('button', { class: 'btn btn-ghost btn-sm', onClick: () => openPromptEditorModal(row.name) }, t('board.editPrompt')));
    }
    rowChildren.push(el('button', {
      class: 'btn-icon danger',
      title: t('board.deleteColumn'),
      'aria-label': t('workflow.deleteRowAria', { name: row.name || t('workflow.columnFallback') }),
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
      showToast(t('workflow.columnNameEmpty'), 'error');
      return;
    }
    const lowerNames = draft.map((r) => r.name.trim().toLowerCase());
    if (new Set(lowerNames).size !== lowerNames.length) {
      showToast(t('workflow.columnNamesUnique'), 'error');
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
      el('h3', null, t('settings.agentPolicy')),
      el('div', { class: 'kv-grid' }, [
        kv(t('issue.agentKind'), agent.kind),
        kv(t('chat.maxTurns'), String(agent.max_turns)),
        kv(t('settings.maxConcurrent'), String(agent.max_concurrent_agents)),
        kv(t('settings.maxAttempts'), String(agent.max_attempts)),
        kv(
          t('settings.mergeDelivery'),
          agent.merge_delivery === 'local-only' || agent.auto_merge_push_target === false
            ? t('settings.mergeDeliveryLocal')
            : t('settings.mergeDeliveryUpstream'),
        ),
      ]),
    ]);
  }

  // ------------------------------------------------------------------
  // Page: Git
  // ------------------------------------------------------------------

  function renderGitPage(container) {
    const page = el('div', { class: 'page page-git' });
    const refreshBtn = el('button', { class: 'btn btn-ghost', onClick: () => renderRoute() }, t('common.refresh'));
    page.appendChild(el('div', { class: 'topbar' }, [el('h1', { class: 'page-title' }, t('nav.git')), refreshBtn]));
    const body = el('div', { class: 'git-body' }, [buildSkeletonBlock()]);
    page.appendChild(body);
    container.appendChild(page);
    loadGitPage(body);
  }

  async function loadGitPage(body) {
    try {
      const [taskData, branchesResp, remoteStatus] = await Promise.all([
        api.getTaskBranches(),
        api.getBranches(),
        api.getGitRemoteStatus().catch(() => ({ remotes: [], gh_available: false })),
      ]);
      state.branches = branchesResp.branches;
      state.gitRemote = remoteStatus;
      clearNode(body);
      if (taskData.note === 'not_a_git_repo') {
        body.appendChild(el('div', { class: 'empty-state' }, t('git.notARepo')));
        return;
      }
      const diffPanel = buildDiffPanel();
      const compareCard = buildGitCompareCard(taskData, diffPanel);
      body.appendChild(el('div', { class: 'git-left' }, [
        buildTaskBranchesCard(taskData, compareCard),
        buildGitHistoryCard(diffPanel),
        compareCard.node,
      ]));
      body.appendChild(diffPanel.node);
    } catch (err) {
      clearNode(body);
      body.appendChild(el('div', { class: 'empty-state' }, t('git.loadFailed', { error: err.message })));
    }
  }

  function buildTaskBranchesCard(data, compareCard) {
    const rows = el('div', { class: 'branch-rows' });
    const branches = data.branches || [];
    if (!branches.length) {
      rows.appendChild(el('div', { class: 'history-muted' }, t('git.noTaskBranches')));
    }
    for (const row of branches) rows.appendChild(buildTaskBranchRow(row, data, compareCard));
    const remote = state.gitRemote || {};
    const targetLine = [
      el('span', { class: 'history-muted' }, t('git.mergeTarget')),
      el('span', { class: 'git-mono' }, data.target_branch || '(unknown)'),
      el('span', { class: 'chip-label' }, data.auto_merge_enabled ? t('git.autoMergeOn') : t('git.autoMergeOff')),
      el(
        'span',
        { class: 'chip-label' },
        data.merge_delivery === 'local-only' || data.auto_merge_push_target === false
          ? t('git.localOnly')
          : t('git.upstreamPublish'),
      ),
    ];
    if (data.target_branch && remote.default_remote) {
      targetLine.push(el('button', {
        class: 'btn btn-ghost btn-sm git-push-target',
        title: t('git.pushBranchTo', { branch: data.target_branch, remote: remote.default_remote }),
        onClick: () => openPushTargetModal(data.target_branch, remote.default_remote),
      }, t('git.pushTarget')));
    }
    return el('div', { class: 'card-panel' }, [
      el('h3', null, t('git.taskBranches')),
      el('div', { class: 'git-target-line' }, targetLine),
      remote.remotes && !remote.remotes.length
        ? el('div', { class: 'history-muted' }, t('git.noRemoteHint'))
        : null,
      rows,
    ]);
  }

  function buildTaskBranchRow(row, data, compareCard) {
    const badges = [];
    if (row.merged) badges.push(el('span', { class: 'badge-merged' }, t('git.badgeMerged')));
    else if (row.ahead != null) badges.push(el('span', { class: 'ahead-behind' }, `↑${row.ahead} ↓${row.behind}`));
    if (row.running) badges.push(el('span', { class: 'badge-running' }, t('git.badgeRunning')));
    const compareBtn = el('button', {
      class: 'btn btn-ghost btn-sm',
      onClick: () => compareCard.load(row.branch),
    }, t('git.compare'));
    const mergeBtn = el('button', {
      class: 'btn btn-primary btn-sm',
      disabled: Boolean(row.merged || row.running),
      onClick: () => openMergeModal(row, data),
    }, t('git.merge'));
    const remote = state.gitRemote || {};
    const hasRemote = Boolean(remote.default_remote);
    const pushBtn = el('button', {
      class: 'btn btn-ghost btn-sm',
      disabled: !hasRemote || Boolean(row.running),
      title: hasRemote ? t('git.pushTo', { remote: remote.default_remote }) : t('git.noRemote'),
      onClick: () => pushTaskBranch(row.branch, remote.default_remote),
    }, t('git.push'));
    const prBtn = el('button', {
      class: 'btn btn-ghost btn-sm',
      disabled: !hasRemote || !remote.gh_available,
      title: remote.gh_available ? t('git.openPrWithGh') : t('git.ghMissing'),
      onClick: () => openPullRequestModal(row, data),
    }, t('git.pr'));
    const deleteBtn = el('button', {
      class: 'btn btn-danger-outline btn-sm',
      disabled: Boolean(row.running),
      title: row.merged ? t('git.deleteMergedBranch') : t('git.deleteUnmergedBranch'),
      onClick: () => openDeleteBranchModal(row, data),
    }, t('common.delete'));
    return el('div', { class: 'branch-row' }, [
      el('div', { class: 'branch-row-main' }, [
        el('span', { class: 'git-mono' }, row.branch),
        row.ticket
          ? el('span', { class: 'chip-label' }, `${row.ticket.identifier} · ${row.ticket.state}`)
          : el('span', { class: 'history-muted' }, t('git.noTicket')),
        ...badges,
      ]),
      el('div', { class: 'branch-row-side' }, [
        el('span', { class: 'run-history-time' }, `${row.last_commit.subject} · ${timeAgo(row.last_commit.date)}`),
        compareBtn,
        mergeBtn,
        pushBtn,
        prBtn,
        deleteBtn,
      ]),
    ]);
  }

  async function pushTaskBranch(branch, remote) {
    try {
      const result = await api.postGitPush({ branch });
      showToast(t('git.pushed', { branch, remote: result.remote || remote }), 'success');
      renderRoute();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // The merge target is what everyone else pulls, so pushing it asks the
  // operator to retype the branch name (the API demands the same token).
  function openPushTargetModal(branch, remote) {
    const confirmInput = el('input', { class: 'input', placeholder: branch });
    openFormModal({
      title: t('git.pushTitle', { branch }),
      body: el('div', { class: 'form-stack' }, [
        el('p', { class: 'confirm-message' },
          t('git.pushSharedWarning', { branch, remote })),
        field(t('git.typeToConfirm', { branch }), confirmInput),
      ]),
      submitLabel: t('git.push'),
      onSubmit: async () => {
        const result = await api.postGitPush({ branch, confirm: confirmInput.value.trim() });
        showToast(t('git.pushed', { branch, remote: result.remote }), 'success');
      },
    });
  }

  function openDeleteBranchModal(row, data) {
    const forceCheckbox = el('input', { type: 'checkbox', id: 'git-delete-force' });
    forceCheckbox.checked = !row.merged;
    openFormModal({
      title: t('git.deleteTitle', { branch: row.branch }),
      body: el('div', { class: 'form-stack' }, [
        el('p', { class: 'confirm-message' }, row.merged
          ? t('git.deleteMergedConfirm', { branch: row.branch, target: data.target_branch || t('git.theTarget') })
          : t('git.deleteUnmergedConfirm', { branch: row.branch, target: data.target_branch || t('git.theTarget') })),
        el('div', { class: 'form-row-inline' }, [
          forceCheckbox,
          el('label', { for: 'git-delete-force' }, t('git.forceDelete')),
        ]),
      ]),
      submitLabel: t('git.deleteBranch'),
      onSubmit: async () => {
        await api.postGitBranchDelete({ branch: row.branch, force: forceCheckbox.checked });
        showToast(t('git.deleted', { branch: row.branch }), 'success');
        renderRoute();
      },
    });
  }

  function openPullRequestModal(row, data) {
    const targetSelect = buildBranchSelect(data.target_branch || '');
    const titleInput = el('input', {
      class: 'input',
      value: row.ticket ? `${row.ticket.identifier}: ${row.ticket.title}` : row.identifier,
    });
    const bodyInput = el('textarea', { class: 'textarea', rows: 4 },
      row.ticket ? t('git.prBody', { id: row.ticket.identifier }) : '');
    openFormModal({
      title: t('git.prTitle', { branch: row.branch }),
      body: el('div', { class: 'form-stack' }, [
        el('p', { class: 'confirm-message' }, t('git.prHint')),
        field(t('git.baseBranch'), targetSelect),
        field(t('common.title'), titleInput),
        field(t('common.body'), bodyInput),
      ]),
      submitLabel: t('git.createPr'),
      onSubmit: async () => {
        const payload = { branch: row.branch, title: titleInput.value.trim(), body: bodyInput.value };
        if (targetSelect.value) payload.target = targetSelect.value;
        const result = await api.postGitPullRequest(payload);
        showToast(result.url ? t('git.prCreatedWithUrl', { url: result.url }) : t('git.prCreated'), 'success');
      },
    });
  }

  function openMergeModal(row, data) {
    const targetSelect = buildBranchSelect(data.target_branch || '');
    const summary = el('p', { class: 'confirm-message' },
      t('git.mergeHint', { branch: row.branch }));
    openFormModal({
      title: t('git.mergeTitle', { branch: row.branch }),
      body: el('div', null, [summary, field(t('git.targetBranch'), targetSelect)]),
      submitLabel: t('git.merge'),
      onSubmit: async () => {
        const payload = { branch: row.branch };
        if (targetSelect.value) payload.target = targetSelect.value;
        const result = await api.postGitMerge(payload);
        showToast(t('git.merged', { branch: row.branch, target: result.target }), 'success');
        if (row.ticket && isBlockedState(row.ticket.state)) {
          showToast(t('issue.blockedHint'), 'info');
        }
        renderRoute();
      },
    });
  }

  function buildGitHistoryCard(diffPanel) {
    const options = [el('option', { value: '' }, t('git.allBranches'))];
    for (const branch of state.branches) options.push(el('option', { value: branch }, branch));
    const branchSelect = el('select', { class: 'select' }, options);
    const rows = el('div', { class: 'commit-rows' });
    branchSelect.addEventListener('change', () => loadGitHistory(branchSelect.value, rows, diffPanel));
    loadGitHistory('', rows, diffPanel);
    return el('div', { class: 'card-panel' }, [
      el('h3', null, t('git.history')),
      field(t('common.branch'), branchSelect),
      rows,
    ]);
  }

  async function loadGitHistory(branch, rows, diffPanel) {
    clearNode(rows);
    rows.appendChild(el('div', { class: 'history-muted' }, t('git.loadingCommits')));
    try {
      const data = await api.getGitLog(branch ? { branch, limit: 50 } : { limit: 50 });
      clearNode(rows);
      const commits = data.commits || [];
      if (!commits.length) {
        rows.appendChild(el('div', { class: 'history-muted' }, t('git.noCommits')));
        return;
      }
      for (const commit of commits) rows.appendChild(buildCommitRow(commit, diffPanel));
    } catch (err) {
      clearNode(rows);
      rows.appendChild(el('div', { class: 'history-muted' }, `History unavailable: ${err.message}`));
    }
  }

  function buildCommitRow(commit, diffPanel) {
    const refs = (commit.refs || []).map((ref) => el('span', { class: 'ref-chip' }, ref));
    const attrs = { class: 'commit-row' };
    if (diffPanel) {
      attrs.class += ' clickable';
      attrs.title = t('git.showFileChanges');
      // Keyboard parity with the click — a real tab stop so the commit's
      // diff is reachable without a mouse.
      attrs.tabindex = '0';
      attrs.role = 'button';
      attrs.onClick = () => diffPanel.showCommit(commit);
      attrs.onKeydown = (e) => {
        if (boardIsStale()) return;
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        diffPanel.showCommit(commit);
      };
    }
    return el('div', attrs, [
      el('span', { class: 'git-mono commit-sha' }, commit.short_sha),
      el('span', { class: 'commit-subject' }, commit.subject),
      ...refs,
      el('span', { class: 'run-history-time' }, `${commit.author} · ${timeAgo(commit.date)}`),
    ]);
  }

  function buildGitCompareCard(data, diffPanel) {
    const branchOptions = [el('option', { value: '' }, t('git.pickBranch'))];
    for (const branch of state.branches) branchOptions.push(el('option', { value: branch }, branch));
    const branchSelect = el('select', { class: 'select' }, branchOptions);
    const targetSelect = buildBranchSelect(data.target_branch || '');
    const resultBox = el('div', { class: 'compare-result' }, [
      el('div', { class: 'history-muted' }, t('git.pickBranchHint')),
    ]);
    const loadBtn = el('button', { class: 'btn btn-ghost btn-sm', onClick: () => doLoad() }, t('common.load'));
    const node = el('div', { class: 'card-panel' }, [
      el('h3', null, t('git.compare')),
      fieldRow([field(t('common.branch'), branchSelect), field(t('common.target'), targetSelect)]),
      loadBtn,
      resultBox,
    ]);

    async function doLoad() {
      const branch = branchSelect.value;
      if (!branch) return;
      clearNode(resultBox);
      resultBox.appendChild(el('div', { class: 'history-muted' }, t('git.comparing')));
      try {
        const params = { branch };
        if (targetSelect.value) params.target = targetSelect.value;
        const cmp = await api.getGitCompare(params);
        clearNode(resultBox);
        resultBox.appendChild(el('div', { class: 'git-target-line' }, [
          el('span', { class: 'git-mono' }, `${cmp.branch} → ${cmp.target}`),
          el('span', { class: 'ahead-behind' }, `↑${cmp.ahead == null ? '?' : cmp.ahead} ↓${cmp.behind == null ? '?' : cmp.behind}`),
          cmp.merged ? el('span', { class: 'badge-merged' }, t('git.badgeMerged')) : null,
        ]));
        const commits = cmp.commits || [];
        for (const commit of commits) resultBox.appendChild(buildCommitRow(commit, diffPanel));
        if (cmp.commits_truncated) {
          resultBox.appendChild(el('div', { class: 'history-muted' }, t('git.commitListTruncated')));
        }
        if (!commits.length) {
          resultBox.appendChild(el('div', { class: 'history-muted' }, t('git.nothingToMerge')));
        }
        resultBox.appendChild(buildDiffstatTable(cmp.stat, diffPanel));
        diffPanel.showCompare(cmp.branch, cmp.target);
      } catch (err) {
        clearNode(resultBox);
        resultBox.appendChild(el('div', { class: 'history-muted' }, `Compare failed: ${err.message}`));
      }
    }

    return {
      node,
      load(branch) {
        branchSelect.value = branch;
        doLoad();
        node.scrollIntoView({ behavior: 'smooth', block: 'start' });
      },
    };
  }

  function buildDiffstatTable(stat, diffPanel) {
    const files = (stat && stat.files) || [];
    if (!files.length) return el('div', { class: 'history-muted' }, t('git.noFileChanges'));
    const total = stat.total || {};
    const rows = files.map((f) => {
      const attrs = {};
      if (diffPanel) {
        attrs.class = 'clickable';
        attrs.title = t('git.jumpToFileDiff');
        attrs.onClick = () => diffPanel.scrollToFile(f.path);
      }
      return el('tr', attrs, [
        el('td', { class: 'diffstat-path' }, f.path),
        el('td', { class: 'stat-add' }, f.binary ? 'bin' : `+${f.insertions}`),
        el('td', { class: 'stat-del' }, f.binary ? '' : `−${f.deletions}`),
      ]);
    });
    rows.push(el('tr', { class: 'diffstat-total' }, [
      el('td', null, t('git.filesCount', { n: total.files || 0 })),
      el('td', { class: 'stat-add' }, `+${total.insertions || 0}`),
      el('td', { class: 'stat-del' }, `−${total.deletions || 0}`),
    ]));
    return el('table', { class: 'diffstat-table' }, [el('tbody', null, rows)]);
  }

  function buildDiffPanel() {
    const subtitle = el('div', { class: 'diff-panel-subtitle history-muted' }, t('git.diffPlaceholder'));
    const bodyBox = el('div', { class: 'diff-panel-body' });
    const node = el('div', { class: 'card-panel git-diff-panel' }, [
      el('h3', null, t('git.changes')),
      subtitle,
      bodyBox,
    ]);

    function setLoading(label) {
      subtitle.textContent = label;
      clearNode(bodyBox);
      bodyBox.appendChild(el('div', { class: 'history-muted' }, t('git.loadingDiff')));
    }

    function showError(message) {
      clearNode(bodyBox);
      bodyBox.appendChild(el('div', { class: 'history-muted' }, `Diff unavailable: ${message}`));
    }

    function renderPatch(patch, truncated) {
      clearNode(bodyBox);
      if (!patch) {
        bodyBox.appendChild(el('div', { class: 'history-muted' }, t('git.noChanges')));
        return;
      }
      const parsed = splitPatchByFile(patch);
      const metaText = parsed.meta.join('\n').trim();
      if (metaText) bodyBox.appendChild(el('pre', { class: 'diff-commit-meta' }, metaText));
      for (const file of parsed.files) bodyBox.appendChild(buildDiffFileSection(file));
      if (truncated) bodyBox.appendChild(el('div', { class: 'history-muted' }, t('git.diffTruncated')));
    }

    async function showCompare(branch, target) {
      setLoading(`${branch} → ${target}`);
      try {
        const data = await api.getGitDiff({ branch, target });
        renderPatch(data.patch, data.truncated);
      } catch (err) {
        showError(err.message);
      }
    }

    async function showCommit(commit) {
      setLoading(t('git.commitLabel', { sha: commit.short_sha, subject: commit.subject }));
      try {
        const data = await api.getGitDiff({ commit: commit.sha });
        renderPatch(data.patch, data.truncated);
      } catch (err) {
        showError(err.message);
      }
    }

    function scrollToFile(path) {
      const section = bodyBox.querySelector(`[data-path="${CSS.escape(path)}"]`);
      if (section) {
        section.open = true;
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    return { node, showCompare, showCommit, scrollToFile };
  }

  function splitPatchByFile(patch) {
    const meta = [];
    const files = [];
    let current = null;
    for (const line of patch.split('\n')) {
      if (line.startsWith('diff --git ')) {
        current = { path: parseDiffPath(line), lines: [line] };
        files.push(current);
      } else if (current) {
        current.lines.push(line);
      } else {
        meta.push(line);
      }
    }
    return { meta, files };
  }

  function parseDiffPath(header) {
    const match = header.match(/ b\/(.+)$/);
    return match ? match[1] : header;
  }

  function buildDiffFileSection(file) {
    const lines = file.lines.map((line) => el('div', { class: `diff-line ${diffLineClass(line)}` }, line || ' '));
    return el('details', { class: 'diff-file', open: true, 'data-path': file.path }, [
      el('summary', { class: 'diff-file-header' }, file.path),
      el('div', { class: 'diff-file-body' }, lines),
    ]);
  }

  function diffLineClass(line) {
    if (
      line.startsWith('diff --git') || line.startsWith('index ') ||
      line.startsWith('+++') || line.startsWith('---') ||
      line.startsWith('new file') || line.startsWith('deleted file') ||
      line.startsWith('similarity') || line.startsWith('rename ') ||
      line.startsWith(t('git.binaryFiles'))
    ) return 'diff-meta';
    if (line.startsWith('@@')) return 'diff-hunk';
    if (line.startsWith('+')) return 'diff-add';
    if (line.startsWith('-')) return 'diff-del';
    return 'diff-ctx';
  }

  // ------------------------------------------------------------------
  // Page: Chat
  // ------------------------------------------------------------------

  const chatState = {
    snapshot: null, busy: false, socket: null, reconnectDelay: 1000, seqSeen: 0, fontSize: 15,
    // Token deltas stream into one live bubble; `liveText` is the source of
    // truth and DOM writes are coalesced per animation frame so a fast turn
    // does not thrash layout once per token.
    liveBubble: null, liveText: '', liveFrame: 0,
    // Several sessions can run at once; the page shows one at a time and
    // tells the socket which one so only its deltas are streamed.
    currentId: null, sessions: null, autoCreatePromise: null, projectSetupActions: {},
    projectSetupExpiryTimers: {}, confirmationTokens: {}, lifecycleBusy: false,
  };

  const CHAT_AGENT_LABELS = {
    agy: 'AGY',
    claude: 'Claude Code',
    codex: 'Codex',
    gemini: 'Gemini CLI',
    kiro: 'Kiro',
    opencode: 'OpenCode',
    pi: 'Pi',
    'prime-agent': 'Prime Agent',
  };

  const CHAT_CONFIRMATION_KEY_PREFIX = 'symphony.chatConfirmation.';

  function newChatConfirmationToken() {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function rememberChatConfirmationToken(sessionId, token) {
    if (!sessionId || !token) return;
    chatState.confirmationTokens[sessionId] = token;
    try {
      localStorage.setItem(`${CHAT_CONFIRMATION_KEY_PREFIX}${sessionId}`, token);
    } catch (_err) { /* memory-only token is still valid until this page closes */ }
  }

  function chatConfirmationToken(sessionId) {
    if (!sessionId) return null;
    if (chatState.confirmationTokens[sessionId]) return chatState.confirmationTokens[sessionId];
    try {
      const token = localStorage.getItem(`${CHAT_CONFIRMATION_KEY_PREFIX}${sessionId}`);
      if (/^[A-Za-z0-9_-]{32,256}$/.test(token || '')) {
        chatState.confirmationTokens[sessionId] = token;
        return token;
      }
    } catch (_err) { /* storage unavailable */ }
    return null;
  }

  async function createChatSessionWithConfirmation(payload) {
    const token = newChatConfirmationToken();
    const snapshot = await api.createChatSession2({ ...payload, confirmation_token: token });
    rememberChatConfirmationToken(snapshot.session_id, token);
    return snapshot;
  }

  const CHAT_FONT_KEY = 'symphony.chatFontSize';
  const CHAT_FONT_MIN = 12;
  const CHAT_FONT_MAX = 20;

  function loadChatFontSize() {
    try {
      const raw = Number(localStorage.getItem(CHAT_FONT_KEY));
      if (raw >= CHAT_FONT_MIN && raw <= CHAT_FONT_MAX) return raw;
    } catch (_err) { /* storage unavailable */ }
    return 15;
  }

  function applyChatFontSize(view) {
    view.transcript.style.fontSize = `${chatState.fontSize}px`;
    view.input.style.fontSize = `${chatState.fontSize}px`;
  }

  function bumpChatFont(view, delta) {
    chatState.fontSize = Math.min(CHAT_FONT_MAX, Math.max(CHAT_FONT_MIN, chatState.fontSize + delta));
    try {
      localStorage.setItem(CHAT_FONT_KEY, String(chatState.fontSize));
    } catch (_err) { /* storage unavailable */ }
    applyChatFontSize(view);
  }

  function buildFontControls(view) {
    return el('div', { class: 'chat-font-controls' }, [
      el('button', { class: 'btn-icon', title: t('chat.smallerText'), 'aria-label': t('chat.decreaseFont'), onClick: () => bumpChatFont(view, -1) }, 'A−'),
      el('button', { class: 'btn-icon', title: t('chat.largerText'), 'aria-label': t('chat.increaseFont'), onClick: () => bumpChatFont(view, 1) }, 'A+'),
    ]);
  }

  function closeChatSocket() {
    if (chatState.socket) {
      const socket = chatState.socket;
      chatState.socket = null;
      socket.onclose = null;
      socket.close();
    }
  }

  function renderChatPage(container) {
    const page = el('div', { class: 'page page-chat' });
    const topActions = el('div', { class: 'chat-top-actions' });
    page.appendChild(el('div', { class: 'topbar' }, [el('h1', { class: 'page-title' }, t('nav.chat')), topActions]));
    const sessionBar = el('div', { class: 'chat-session-bar' });
    const controls = el('div', { class: 'chat-controls' });
    const transcript = el('div', { class: 'chat-transcript' });
    const typing = el('div', { class: 'chat-typing', style: 'display:none;' }, t('issue.agentWorking'));
    const input = el('textarea', {
      class: 'textarea chat-input',
      rows: 2,
      placeholder: t('chat.inputPlaceholder'),
    });
    const sendBtn = el('button', { class: 'btn btn-primary', onClick: () => sendChatMessage(view) }, t('chat.send'));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage(view);
      }
    });
    page.appendChild(el('div', { class: 'chat-body' }, [
      sessionBar,
      controls,
      transcript,
      typing,
      el('div', { class: 'chat-composer' }, [input, sendBtn]),
    ]));
    container.appendChild(page);
    const view = { topActions, sessionBar, controls, transcript, typing, input, sendBtn };
    chatState.fontSize = loadChatFontSize();
    applyChatFontSize(view);
    connectChatSocket(view);
    refreshChatSessions(view);
  }

  function rememberChatProjectSetup(action) {
    if (!action || !action.action_id) return;
    chatState.projectSetupActions[action.action_id] = action;
    if (chatState.snapshot) {
      chatState.snapshot.project_setup_actions = Object.values(chatState.projectSetupActions);
    }
  }

  function forgetChatProjectSetup(actionId) {
    if (!actionId) return;
    const timer = chatState.projectSetupExpiryTimers[actionId];
    if (timer) clearTimeout(timer);
    delete chatState.projectSetupExpiryTimers[actionId];
    delete chatState.projectSetupActions[actionId];
    if (chatState.snapshot) {
      chatState.snapshot.project_setup_actions = Object.values(chatState.projectSetupActions);
    }
  }

  function renderChatProjectSetupAction(view, action) {
    if (!action || !action.action_id) return;
    const existing = view.transcript.querySelector(`[data-project-setup-id="${action.action_id}"]`);
    const node = buildChatProjectSetupNode(view, action);
    if (existing) existing.replaceWith(node);
    else view.transcript.appendChild(node);
  }

  function clearChatProjectSetupActions(view) {
    for (const timer of Object.values(chatState.projectSetupExpiryTimers)) clearTimeout(timer);
    chatState.projectSetupExpiryTimers = {};
    chatState.projectSetupActions = {};
    if (chatState.snapshot) chatState.snapshot.project_setup_actions = [];
    for (const node of view.transcript.querySelectorAll('[data-project-setup-id]')) {
      node.remove();
    }
  }

  function reconcileChatProjectSetupActions(view, snapshot) {
    if (!snapshot || !snapshot.active) {
      clearChatProjectSetupActions(view);
      return;
    }
    const actions = {};
    for (const action of snapshot.project_setup_actions || []) {
      if (action && action.action_id) actions[action.action_id] = action;
    }
    for (const actionId of Object.keys(chatState.projectSetupActions)) {
      if (!actions[actionId]) {
        const timer = chatState.projectSetupExpiryTimers[actionId];
        if (timer) clearTimeout(timer);
        delete chatState.projectSetupExpiryTimers[actionId];
      }
    }
    chatState.projectSetupActions = actions;
    snapshot.project_setup_actions = Object.values(actions);
    for (const node of view.transcript.querySelectorAll('[data-project-setup-id]')) {
      if (!actions[node.dataset.projectSetupId]) node.remove();
    }
    for (const action of Object.values(actions)) renderChatProjectSetupAction(view, action);
  }

  function chatProjectSetupForChoice(text) {
    if (!chatState.snapshot || chatState.snapshot.mode !== 'edit') return null;
    const matches = Object.values(chatState.projectSetupActions).filter((action) =>
      action && action.choice_active &&
      (action.status === 'pending' || action.status === 'failed') &&
      !chatProjectSetupExpired(action) && String(action.choice) === text
    );
    return matches.length === 1 ? matches[0] : null;
  }

  async function selectChatProjectSetup(view, action) {
    const sessionId = chatState.currentId;
    if (!sessionId) throw new ApiError(t('chat.noSessionSelected'), 'chat_no_session', 404);
    const confirmationToken = chatConfirmationToken(sessionId);
    if (!confirmationToken) {
      throw new ApiError(
        t('chat.projectSetupConfirmationUnavailable'),
        'chat_project_confirmation_forbidden',
        403
      );
    }
    try {
      const result = await api.selectChatProjectSetup(
        sessionId, action.action_id, confirmationToken
      );
      if (chatState.currentId !== sessionId) return result && result.action || action;
      if (result && result.action) {
        rememberChatProjectSetup(result.action);
        renderChatProjectSetupAction(view, result.action);
        if (result.action.status === 'succeeded') await loadProjects();
      }
      return chatState.projectSetupActions[action.action_id] || action;
    } catch (err) {
      if (chatState.currentId === sessionId) {
        const failedAction = err.data && err.data.action;
        if (failedAction) {
          rememberChatProjectSetup(failedAction);
          renderChatProjectSetupAction(view, failedAction);
        }
        try {
          const snapshot = await api.getChatSessionById(sessionId);
          if (chatState.currentId === sessionId) {
            chatState.snapshot = snapshot;
            reconcileChatProjectSetupActions(view, snapshot);
          }
        } catch (_refreshErr) { /* preserve the original confirmation error */ }
      }
      throw err;
    }
  }

  async function sendChatMessage(view) {
    const text = view.input.value.trim();
    const sessionId = chatState.currentId;
    if (!text) return;
    try {
      const action = chatProjectSetupForChoice(text);
      if (action) {
        await selectChatProjectSetup(view, action);
        if (chatState.currentId === sessionId) {
          showToast(t('chat.projectSetupSelected'), 'success');
        }
      } else if (sessionId) {
        await api.postChatMessageTo(sessionId, { text });
      } else {
        await api.postChatMessage({ text });
      }
      if (chatState.currentId === sessionId && view.input.value.trim() === text) {
        view.input.value = '';
      }
    } catch (err) {
      if (chatState.currentId === sessionId) {
        showToast(err.message, 'error');
        if (err.code === 'chat_backend_unavailable' || err.code === 'chat_no_session') {
          await refreshChatSessions(view);
        }
      }
    }
  }

  // ---- session bar: live tabs + resumable sessions -------------------

  async function ensureDefaultChatSession() {
    if (!chatState.autoCreatePromise) {
      // Opening Chat should be immediately useful, but must not start an
      // agent turn. Creating an idle QA session is cheap and read-only.
      chatState.autoCreatePromise = createChatSessionWithConfirmation({ mode: 'qa' });
    }
    try {
      return await chatState.autoCreatePromise;
    } finally {
      chatState.autoCreatePromise = null;
    }
  }

  async function refreshChatSessions(view) {
    try {
      chatState.sessions = await api.getChatSessions();
    } catch (_err) {
      chatState.sessions = { sessions: [], resumable: [], active_id: null, max_sessions: 0 };
    }
    let live = chatState.sessions.sessions || [];
    if (!live.length) {
      try {
        const snapshot = await ensureDefaultChatSession();
        chatState.sessions = await api.getChatSessions();
        live = chatState.sessions.sessions || [];
        if (!chatState.sessions.active_id) chatState.sessions.active_id = snapshot.session_id;
      } catch (err) {
        // Keep the successfully fetched resumable-session listing visible when
        // the configured agent cannot initialize an idle default session.
        showToast(err.message, 'error');
      }
    }
    const liveSessions = chatState.sessions.sessions || [];
    if (!liveSessions.some((s) => s.session_id === chatState.currentId)) {
      const fallback = chatState.sessions.active_id || (liveSessions[0] && liveSessions[0].session_id) || null;
      await selectChatSession(view, fallback);
      return;
    }
    renderChatSessionBar(view);
  }

  async function selectChatSession(view, sessionId) {
    chatState.currentId = sessionId;
    focusChatSocket(sessionId);
    if (!sessionId) {
      applyChatSnapshot(view, { active: false });
      renderChatSessionBar(view);
      return;
    }
    try {
      const snapshot = await api.getChatSessionById(sessionId);
      if (chatState.currentId !== sessionId) return;
      applyChatSnapshot(view, snapshot);
    } catch (_err) {
      if (chatState.currentId !== sessionId) return;
      applyChatSnapshot(view, { active: false });
    }
    renderChatSessionBar(view);
  }

  function chatSessionLabel(meta) {
    return truncate(meta.title || t('chat.sessionTitleFallback', { mode: meta.mode }), 28);
  }

  function renderChatSessionBar(view) {
    clearNode(view.sessionBar);
    clearNode(view.topActions);
    const listing = chatState.sessions || { sessions: [], resumable: [], max_sessions: 0 };
    const live = listing.sessions || [];
    const tabs = el('div', { class: 'chat-tabs' }, live.map((meta) => el('button', {
      class: `chat-tab${meta.session_id === chatState.currentId ? ' active' : ''}`,
      'data-session-id': meta.session_id,
      disabled: chatState.lifecycleBusy,
      title: t('chat.sessionMeta', { agent: meta.agent_kind, mode: meta.mode, time: formatShortDateTime(meta.created_at) }),
      onClick: () => selectChatSession(view, meta.session_id),
    }, [
      el('span', { class: `chat-tab-dot${meta.busy ? ' busy' : ''}` }),
      chatSessionLabel(meta),
    ])));
    view.sessionBar.appendChild(tabs);
    const atLimit = listing.max_sessions > 0 && live.length >= listing.max_sessions;
    view.sessionBar.appendChild(el('button', {
      class: 'btn btn-ghost chat-new-session',
      disabled: atLimit || chatState.lifecycleBusy,
      title: atLimit ? t('chat.sessionLimit', { max: listing.max_sessions }) : t('chat.startAnother'),
      onClick: () => openNewChatSessionModal(view),
    }, t('chat.newSessionShort')));
    const resumable = listing.resumable || [];
    if (resumable.length) view.sessionBar.appendChild(buildChatResumeControl(view, resumable, atLimit));
    view.topActions.appendChild(buildFontControls(view));
  }

  function buildChatResumeControl(view, resumable, atLimit) {
    const select = el('select', { class: 'select chat-resume-select' }, [
      el('option', { value: '' }, t('chat.resumeCount', { n: resumable.length })),
      ...resumable.map((meta) => el('option', { value: meta.session_id },
        `${truncate(meta.title || meta.mode, 30)} · ${formatShortDateTime(meta.updated_at || meta.created_at)}`)),
    ]);
    select.disabled = atLimit || chatState.lifecycleBusy;
    select.addEventListener('change', async () => {
      const sessionId = select.value;
      select.value = '';
      if (!sessionId || chatState.lifecycleBusy) return;
      setChatLifecycleBusy(view, true);
      try {
        let confirmationToken = chatConfirmationToken(sessionId);
        if (!confirmationToken) {
          confirmationToken = newChatConfirmationToken();
          rememberChatConfirmationToken(sessionId, confirmationToken);
        }
        const snapshot = await api.reattachChatSession(sessionId, confirmationToken);
        showToast(t('chat.sessionReattached'), 'success');
        await refreshChatSessions(view);
        await selectChatSession(view, snapshot.session_id);
      } catch (err) {
        showToast(err.message, 'error');
        if (err.code === 'chat_no_session') await refreshChatSessions(view);
      } finally {
        setChatLifecycleBusy(view, false);
      }
    });
    return select;
  }

  function openNewChatSessionModal(view) {
    const listing = chatState.sessions || {};
    const supportedKinds = listing.supported_agent_kinds || Object.keys(CHAT_AGENT_LABELS);
    const agentSelect = el('select', { class: 'select' }, supportedKinds.map((kind) =>
      el('option', { value: kind }, CHAT_AGENT_LABELS[kind] || kind)
    ));
    const defaultKind = listing.default_agent_kind || 'claude';
    if (supportedKinds.includes(defaultKind)) agentSelect.value = defaultKind;
    const modeSelect = el('select', { class: 'select' }, [
      el('option', { value: 'qa' }, t('chat.qaReadOnly')),
      el('option', { value: 'edit' }, t('chat.editCoworking')),
    ]);
    const turnsInput = el('input', { class: 'input chat-max-turns-input', type: 'number', min: '0', value: '50' });
    const tokensInput = el('input', { class: 'input chat-max-tokens-input', type: 'number', min: '0', step: '1000', value: '1000000' });
    openFormModal({
      title: t('chat.newSession'),
      body: el('div', { class: 'form-stack' }, [
        field(t('common.agent'), agentSelect),
        field(t('common.mode'), modeSelect),
        fieldRow([
          field(t('chat.warnAfterTurns'), turnsInput),
          field(t('chat.warnAfterTokens'), tokensInput),
        ]),
        el('p', { class: 'form-hint' }, t('chat.budgetHint')),
      ]),
      submitLabel: t('chat.startSession'),
      onSubmit: async () => {
        const snapshot = await createChatSessionWithConfirmation({
          agent_kind: agentSelect.value,
          mode: modeSelect.value,
          max_turns: Math.max(0, Number(turnsInput.value) || 0),
          max_tokens: Math.max(0, Number(tokensInput.value) || 0),
        });
        await refreshChatSessions(view);
        await selectChatSession(view, snapshot.session_id);
      },
    });
  }

  function setChatLifecycleBusy(view, busy) {
    chatState.lifecycleBusy = busy;
    renderChatSessionBar(view);
    renderChatControls(view);
    updateChatComposer(view);
  }

  function updateChatComposer(view) {
    const snap = chatState.snapshot || { active: false };
    const disabled = !snap.active || chatState.busy || chatState.lifecycleBusy;
    view.input.disabled = disabled;
    view.sendBtn.disabled = disabled;
  }

  // Advisory only — the chip turns red at the limit but nothing is blocked.
  function buildChatBudgetChip(budget) {
    const turns = budget.max_turns
      ? t('chat.turnsUsedMax', { used: budget.turn_count, max: budget.max_turns })
      : t('chat.turnsUsedOnly', { used: budget.turn_count });
    const tokens = budget.max_tokens
      ? t('chat.tokensUsedMax', { used: formatCompactNumber(budget.used_tokens), max: formatCompactNumber(budget.max_tokens) })
      : t('chat.tokensUsedOnly', { used: formatCompactNumber(budget.used_tokens) });
    return el('span', {
      class: `chat-budget-chip${budget.exceeded ? ' over' : ''}`,
      title: budget.exceeded ? t('chat.budgetReached') : t('chat.usageHint'),
    }, `${turns} · ${tokens}`);
  }

  function renderChatControls(view) {
    clearNode(view.controls);
    const snap = chatState.snapshot || { active: false };
    if (!snap.active) {
      // Creating and reattaching sessions lives in the session bar.
      view.controls.appendChild(el('span', { class: 'chat-hint' },
        t('chat.noSessionSelected')));
      return;
    }
    view.controls.appendChild(el('span', { class: 'chip-label' }, snap.agent_kind));
    if (snap.budget) view.controls.appendChild(buildChatBudgetChip(snap.budget));
    if (!snap.mode_enforced) {
      view.controls.appendChild(el('span', { class: 'chat-mode-warning' }, t('chat.readOnlyNotEnforced')));
    }
    const toggle = el('div', { class: 'chat-mode-toggle' }, ['qa', 'edit'].map((mode) => el('button', {
      class: `chat-mode-btn${snap.mode === mode ? ' active' : ''}`,
      disabled: chatState.lifecycleBusy,
      onClick: async () => {
        if (snap.mode === mode || chatState.lifecycleBusy) return;
        setChatLifecycleBusy(view, true);
        try {
          const result = await api.patchChatSessionById(snap.session_id, { mode });
          if (!result.context_preserved) showToast(t('chat.modeResetContext'), 'info');
          await refreshChatControls(view);
        } catch (err) {
          showToast(err.message, 'error');
          await refreshChatSessions(view);
        } finally {
          setChatLifecycleBusy(view, false);
        }
      },
    }, mode === 'qa' ? t('chat.qa') : t('common.edit'))));
    view.controls.appendChild(toggle);
    view.controls.appendChild(el('button', {
      class: 'btn btn-ghost',
      disabled: chatState.lifecycleBusy,
      title: t('chat.stopHint'),
      onClick: async () => {
        if (chatState.lifecycleBusy) return;
        setChatLifecycleBusy(view, true);
        try {
          await api.deleteChatSessionById(snap.session_id);
          await refreshChatSessions(view);
        } catch (err) {
          showToast(err.message, 'error');
          if (err.code === 'chat_no_session') await refreshChatSessions(view);
        } finally {
          setChatLifecycleBusy(view, false);
        }
      },
    }, t('chat.stop')));
  }

  async function refreshChatControls(view) {
    const sessionId = chatState.currentId;
    try {
      const snapshot = sessionId
        ? await api.getChatSessionById(sessionId)
        : await api.getChatSession();
      if (chatState.currentId !== sessionId) return;
      chatState.snapshot = snapshot;
      chatState.busy = Boolean(snapshot.busy);
      reconcileChatProjectSetupActions(view, snapshot);
    } catch (_err) {
      if (chatState.currentId !== sessionId) return;
      chatState.snapshot = { active: false };
      chatState.busy = false;
      clearChatProjectSetupActions(view);
    }
    renderChatControls(view);
    updateChatComposer(view);
  }

  function applyChatSnapshot(view, snapshot) {
    chatState.snapshot = snapshot;
    for (const timer of Object.values(chatState.projectSetupExpiryTimers)) clearTimeout(timer);
    chatState.projectSetupExpiryTimers = {};
    chatState.projectSetupActions = {};
    for (const action of snapshot.project_setup_actions || []) rememberChatProjectSetup(action);
    if (snapshot.session_id) chatState.currentId = snapshot.session_id;
    chatState.busy = Boolean(snapshot.busy);
    chatState.seqSeen = 0;
    chatState.liveBubble = null;
    chatState.liveText = '';
    renderChatControls(view);
    clearNode(view.transcript);
    const tail = snapshot.transcript_tail || [];
    for (const msg of tail) appendChatMessage(view, msg, true);
    for (const action of Object.values(chatState.projectSetupActions)) {
      renderChatProjectSetupAction(view, action);
    }
    if (!snapshot.active && !tail.length) {
      view.transcript.appendChild(el('div', { class: 'empty-state' }, t('chat.startHint')));
    }
    updateChatComposer(view);
  }

  function appendChatDelta(view, text) {
    if (!text) return;
    if (!chatState.liveBubble) {
      const bubble = el('div', { class: 'chat-bubble chat-bubble-live' });
      view.transcript.appendChild(el('div', { class: 'chat-msg chat-agent' }, [bubble]));
      chatState.liveBubble = bubble;
      chatState.liveText = '';
    }
    chatState.liveText += text;
    if (chatState.liveFrame) return;
    chatState.liveFrame = requestAnimationFrame(() => {
      chatState.liveFrame = 0;
      if (!chatState.liveBubble) return;
      chatState.liveBubble.textContent = chatState.liveText;
      view.transcript.scrollTop = view.transcript.scrollHeight;
    });
  }

  // Pi and Prime Agent emit the full assistant text on every update rather
  // than token deltas. Replace the live source so cumulative snapshots do not
  // render as "HHeHelHello".
  function replaceChatLive(view, text) {
    if (!text) return;
    if (!chatState.liveBubble) {
      appendChatDelta(view, text);
      return;
    }
    chatState.liveText = text;
    if (chatState.liveFrame) return;
    chatState.liveFrame = requestAnimationFrame(() => {
      chatState.liveFrame = 0;
      if (!chatState.liveBubble) return;
      chatState.liveBubble.textContent = chatState.liveText;
      view.transcript.scrollTop = view.transcript.scrollHeight;
    });
  }

  // Streaming text is plain text; the finished message is the same content as
  // markdown, so it replaces the live bubble instead of duplicating it.
  function finalizeChatLive(view, finalText) {
    const bubble = chatState.liveBubble;
    if (!bubble) return false;
    const text = finalText != null ? finalText : chatState.liveText;
    chatState.liveBubble = null;
    chatState.liveText = '';
    if (chatState.liveFrame) {
      cancelAnimationFrame(chatState.liveFrame);
      chatState.liveFrame = 0;
    }
    clearNode(bubble);
    bubble.classList.remove('chat-bubble-live');
    bubble.appendChild(renderMarkdown(text));
    view.transcript.scrollTop = view.transcript.scrollHeight;
    return true;
  }

  function appendChatMessage(view, msg, fromSnapshot = false) {
    if (msg.type === 'agent_delta') {
      appendChatDelta(view, msg.text);
      return;
    }
    if (msg.type === 'agent_snapshot') {
      replaceChatLive(view, msg.text);
      return;
    }
    if (msg.seq != null) {
      if (msg.seq <= chatState.seqSeen) return;
      chatState.seqSeen = msg.seq;
    }
    if (msg.type === 'project_setup_removed') {
      // The JSONL transcript is not control-plane state. Snapshot replay
      // already starts from the server's current action set.
      if (!fromSnapshot) {
        const actionId = msg.meta && msg.meta.project_setup_action_id;
        if (actionId) {
          forgetChatProjectSetup(actionId);
          view.transcript.querySelector(`[data-project-setup-id="${actionId}"]`)?.remove();
        }
      }
      return;
    }
    if (msg.type === 'project_setup_action' || msg.type === 'project_setup_status' ||
        msg.type === 'project_setup_completed' || msg.type === 'project_setup_failed' ||
        msg.type === 'project_setup_expired') {
      let action = msg.meta && msg.meta.project_setup;
      if (action && fromSnapshot) {
        // Transcript files live in the editable workflow tree. On replay,
        // only the live server snapshot may render a confirmation card.
        action = chatState.projectSetupActions[action.action_id] || null;
      }
      if (action) {
        if (!fromSnapshot) rememberChatProjectSetup(action);
        renderChatProjectSetupAction(view, action);
        view.transcript.scrollTop = view.transcript.scrollHeight;
      }
      return;
    }
    if (msg.type === 'user_message') {
      chatState.busy = true;
      updateChatComposer(view);
    } else if (msg.type === 'turn_started') {
      view.typing.style.display = '';
    } else if (msg.type === 'turn_completed' || msg.type === 'turn_failed') {
      finalizeChatLive(view, null);
      view.typing.style.display = 'none';
      chatState.busy = false;
      if (msg.meta && msg.meta.budget && chatState.snapshot) {
        chatState.snapshot.budget = msg.meta.budget;
        renderChatControls(view);
      }
      updateChatComposer(view);
    } else if (msg.type === 'session_status') {
      refreshChatControls(view);
      // Lifecycle notices carry text; the per-turn agent-session echo does
      // not, and re-listing sessions on every turn would be wasteful.
      if (msg.text) refreshChatSessions(view);
    }
    if (msg.type === 'agent_message' && finalizeChatLive(view, msg.text)) return;
    const node = buildChatMessageNode(msg);
    if (node) {
      view.transcript.appendChild(node);
      view.transcript.scrollTop = view.transcript.scrollHeight;
    }
  }

  function chatProjectSetupExpired(action) {
    const expiresAt = Date.parse(action.expires_at || '');
    return !Number.isFinite(expiresAt) || expiresAt <= Date.now();
  }

  function chatProjectSetupStatus(action) {
    const statuses = {
      pending: t('chat.projectSetupPending'),
      running: t('chat.projectSetupRunning'),
      succeeded: t('chat.projectSetupSucceeded'),
      failed: t('chat.projectSetupFailed'),
      expired: t('chat.projectSetupExpired'),
    };
    return statuses[action.status] || action.status;
  }

  function chatProjectSetupOperation(action) {
    const operations = {
      create: 'chat.projectSetupCreate',
      initialize: 'chat.projectSetupInitialize',
      adopt: 'chat.projectSetupAdopt',
    };
    const key = operations[action.operation];
    return key ? t(key) : '';
  }

  function scheduleChatProjectSetupExpiry(view, action) {
    if (!action || !action.action_id) return;
    const existing = chatState.projectSetupExpiryTimers[action.action_id];
    if (existing) clearTimeout(existing);
    delete chatState.projectSetupExpiryTimers[action.action_id];
    const expiresAt = Date.parse(action.expires_at || '');
    const delay = expiresAt - Date.now();
    if (!Number.isFinite(delay) || delay <= 0 || delay > 2_147_000_000) return;
    chatState.projectSetupExpiryTimers[action.action_id] = setTimeout(() => {
      delete chatState.projectSetupExpiryTimers[action.action_id];
      const current = chatState.projectSetupActions[action.action_id];
      if (current && chatProjectSetupExpired(current)) {
        renderChatProjectSetupAction(view, current);
      }
    }, delay + 1);
  }

  function buildChatProjectSetupNode(view, action) {
    const expired = chatProjectSetupExpired(action);
    const status = expired && (action.status === 'pending' || action.status === 'failed')
      ? 'expired' : (action.status || 'pending');
    const project = action.project || null;
    const canSelect = (status === 'pending' || status === 'failed') &&
      !chatProjectSetupExpired(action) &&
      chatState.snapshot && chatState.snapshot.mode === 'edit' &&
      Boolean(chatConfirmationToken(chatState.currentId));
    const actionButton = canSelect
      ? el('button', {
        class: 'btn btn-primary btn-sm chat-project-setup-select',
        type: 'button',
        onClick: async (event) => {
          const button = event.currentTarget;
          const sessionId = chatState.currentId;
          button.disabled = true;
          try {
            await selectChatProjectSetup(view, action);
            if (chatState.currentId === sessionId) {
              showToast(t('chat.projectSetupSelected'), 'success');
            }
          } catch (err) {
            if (chatState.currentId === sessionId) {
              showToast(err.message, 'error');
              button.disabled = false;
            }
          }
        },
      }, t('chat.projectSetupSelect', { choice: action.choice }))
      : null;
    const node = el('section', {
      class: `chat-project-setup ${status}`,
      'data-project-setup-id': action.action_id,
      'aria-live': 'polite',
    }, [
      el('div', { class: 'chat-project-setup-heading' }, [
        el('strong', null, t('chat.projectSetupTitle', { choice: action.choice })),
        el('span', { class: `chat-project-setup-status ${status}` },
          chatProjectSetupStatus({ ...action, status })),
      ]),
      el('p', { class: 'chat-project-setup-name' }, action.name),
      el('code', { class: 'chat-project-setup-path' }, action.path),
      chatProjectSetupOperation(action)
        ? el('p', { class: 'chat-project-setup-operation' }, chatProjectSetupOperation(action))
        : null,
      project ? el('p', { class: 'chat-project-setup-result' },
        t('chat.projectSetupRegistered', { id: project.id || action.name })) : null,
      action.error ? el('p', { class: 'chat-project-setup-error', role: 'alert' }, action.error) : null,
      actionButton,
    ]);
    scheduleChatProjectSetupExpiry(view, action);
    return node;
  }

  function buildChatMessageNode(msg) {
    switch (msg.type) {
      case 'user_message':
        return el('div', { class: 'chat-msg chat-user' }, [el('div', { class: 'chat-bubble' }, msg.text)]);
      case 'agent_message': {
        const bubble = el('div', { class: 'chat-bubble' });
        bubble.appendChild(renderMarkdown(msg.text));
        return el('div', { class: 'chat-msg chat-agent' }, [bubble]);
      }
      case 'tool_activity': {
        const detail = msg.meta && msg.meta.detail;
        return el('div', { class: 'chat-tool' }, [
          el('span', { class: 'chat-tool-name' }, msg.text),
          detail ? el('span', { class: 'chat-tool-detail' }, detail) : null,
        ]);
      }
      case 'turn_failed':
        return el('div', { class: 'chat-msg chat-error' }, msg.text || t('chat.turnFailed'));
      case 'session_status':
        return msg.text ? el('div', { class: 'chat-status' }, msg.text) : null;
      default:
        return null;
    }
  }

  function focusChatSocket(sessionId) {
    const socket = chatState.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: 'focus', session_id: sessionId || null }));
  }

  function connectChatSocket(view) {
    closeChatSocket();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    // The API guard cannot read headers browsers never send on a WS
    // handshake, so token mode authenticates this one route via ?token=.
    const params = [];
    if (chatState.currentId) params.push(`session=${encodeURIComponent(chatState.currentId)}`);
    const token = storedApiToken();
    if (token) params.push(`token=${encodeURIComponent(token)}`);
    const query = params.length ? `?${params.join('&')}` : '';
    const socket = new WebSocket(`${proto}://${location.host}/api/v1/chat/ws${query}`);
    chatState.socket = socket;
    socket.onopen = () => {
      chatState.reconnectDelay = 1000;
      focusChatSocket(chatState.currentId);
    };
    socket.onmessage = (event) => {
      let frame = null;
      try {
        frame = JSON.parse(event.data);
      } catch (_err) {
        return;
      }
      if (frame.type === 'hello') {
        chatState.sessions = frame.sessions || chatState.sessions;
        if (!chatState.currentId && frame.snapshot && frame.snapshot.session_id) {
          chatState.currentId = frame.snapshot.session_id;
        }
        applyChatSnapshot(view, frame.snapshot || { active: false });
        renderChatSessionBar(view);
        return;
      }
      // Frames from a session the page is not showing only affect the tabs.
      if (frame.session_id && chatState.currentId && frame.session_id !== chatState.currentId) {
        if (frame.type === 'session_status' || frame.type === 'turn_completed' || frame.type === 'user_message') {
          refreshChatSessions(view);
        }
        return;
      }
      appendChatMessage(view, frame);
    };
    socket.onclose = () => {
      if (chatState.socket !== socket) return;
      chatState.socket = null;
      const delay = chatState.reconnectDelay;
      chatState.reconnectDelay = Math.min(delay * 2, 10000);
      setTimeout(() => {
        if (state.route === 'chat' && !chatState.socket) connectChatSocket(view);
      }, delay);
    };
  }

  // ------------------------------------------------------------------
  // Page: Product Preview
  // ------------------------------------------------------------------

  let previewPollTimer = null;

  function cancelPreviewPoll() {
    if (previewPollTimer != null) {
      clearTimeout(previewPollTimer);
      previewPollTimer = null;
    }
  }

  function safePreviewUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(String(value), window.location.href);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
    } catch (_err) {
      return null;
    }
  }

  function previewPhase(data) {
    const phase = String((data && data.phase) || '').toLowerCase();
    if (data && data.running) {
      if (data.healthy || data.ready) return 'running';
      if (phase === 'unhealthy') return 'unhealthy';
      return phase === 'failed' ? 'failed' : 'starting';
    }
    if (phase === 'failed' || (data && data.last_error)) return 'failed';
    return phase || 'stopped';
  }

  function previewPhaseLabel(phase) {
    const labels = {
      running: t('preview.phase.running'),
      starting: t('preview.phase.starting'),
      stopping: t('preview.phase.stopping'),
      unhealthy: t('preview.phase.unhealthy'),
      failed: t('preview.phase.failed'),
      stopped: t('preview.phase.stopped'),
      disabled: t('preview.phase.disabled'),
    };
    return labels[phase] || phase;
  }

  function previewValue(value) {
    return value == null || value === '' ? '—' : String(value);
  }

  function buildPreviewMetric(label, value, extraClass) {
    return el('div', { class: `preview-metric${extraClass ? ` ${extraClass}` : ''}` }, [
      el('dt', null, label),
      el('dd', null, previewValue(value)),
    ]);
  }

  function buildPreviewActions(data, body) {
    const configured = Boolean(data.configured);
    const enabled = data.enabled !== false;
    const running = Boolean(data.running);
    const gateReady = !data.release_gate || data.release_gate.ready !== false;
    const url = safePreviewUrl(data.url);

    async function act(action, button) {
      button.disabled = true;
      body.setAttribute('aria-busy', 'true');
      try {
        const next = await api[action]();
        showToast(t(`preview.${action === 'startPreview' ? 'launched' : action === 'stopPreview' ? 'stopped' : 'restarted'}`), 'success');
        paintPreviewPage(body, next);
      } catch (err) {
        showToast(err.message, 'error');
        await refreshPreviewPage(body, false);
      } finally {
        body.removeAttribute('aria-busy');
      }
    }

    const launch = el('button', {
      class: 'btn btn-primary preview-launch',
      type: 'button',
      disabled: !configured || !enabled || !gateReady || running,
      onClick: (event) => act('startPreview', event.currentTarget),
    }, t('preview.launch'));
    const restart = el('button', {
      class: 'btn',
      type: 'button',
      disabled: !configured || !enabled || !gateReady || !running,
      onClick: (event) => act('restartPreview', event.currentTarget),
    }, t('preview.restart'));
    const stop = el('button', {
      class: 'btn btn-danger-outline',
      type: 'button',
      disabled: !running,
      onClick: (event) => act('stopPreview', event.currentTarget),
    }, t('preview.stop'));
    const open = url && running
      ? el('a', {
        class: 'btn preview-open',
        href: url,
        target: '_blank',
        rel: 'noopener noreferrer',
        'aria-label': t('preview.openAria'),
      }, t('preview.open'))
      : el('button', { class: 'btn preview-open', type: 'button', disabled: true }, t('preview.open'));

    return el('div', { class: 'preview-actions', 'aria-label': t('preview.controls') }, [launch, open, restart, stop]);
  }

  function buildPreviewNotice(data) {
    let title = '';
    let detail = '';
    let tone = 'info';
    if (!data.configured) {
      title = t('preview.unconfigured');
      detail = data.last_error || t('preview.unconfiguredHint');
      tone = 'warning';
    } else if (data.enabled === false) {
      title = t('preview.disabled');
      detail = t('preview.disabledHint');
      tone = 'warning';
    } else if (previewPhase(data) === 'failed') {
      title = t('preview.failed');
      detail = data.last_error || t('preview.failedHint');
      tone = 'danger';
    } else if (data.running && !data.ready) {
      title = t('preview.notReady');
      detail = data.last_error || t('preview.notReadyHint');
      tone = 'warning';
    }
    if (!title) return null;
    return el('section', { class: `preview-notice ${tone}`, role: tone === 'danger' ? 'alert' : 'status' }, [
      el('strong', null, title),
      el('span', null, detail),
    ]);
  }

  function buildPreviewStage(data) {
    const url = safePreviewUrl(data.url);
    if (data.running && url) {
      return el('section', { class: 'preview-stage', 'aria-label': t('preview.productFrame') }, [
        el('div', { class: 'preview-browser-bar' }, [
          el('span', { class: 'preview-browser-light red', 'aria-hidden': 'true' }),
          el('span', { class: 'preview-browser-light amber', 'aria-hidden': 'true' }),
          el('span', { class: 'preview-browser-light green', 'aria-hidden': 'true' }),
          el('span', { class: 'preview-address' }, url),
        ]),
        el('iframe', {
          class: 'preview-frame',
          src: url,
          title: t('preview.iframeTitle'),
          loading: 'eager',
          referrerpolicy: 'no-referrer',
        }),
      ]);
    }
    return el('section', { class: 'preview-stage preview-stage-empty', 'aria-label': t('preview.productFrame') }, [
      el('div', { class: 'preview-empty-mark', 'aria-hidden': 'true' }, '▱'),
      el('h2', null, data.configured ? t('preview.awaitingLaunch') : t('preview.noTarget')),
      el('p', null, data.configured ? t('preview.awaitingLaunchHint') : t('preview.noTargetHint')),
    ]);
  }

  function buildPreviewAcceptance(data) {
    const items = Array.isArray(data.acceptance) ? data.acceptance : [];
    const gate = data.release_gate || {};
    const gateReady = gate.ready === true;
    const gateState = gate.state || (gateReady ? t('preview.gateReady') : t('preview.gatePending'));
    const list = items.length
      ? el('ul', { class: 'preview-checklist' }, items.map((item) =>
        el('li', null, [el('span', { class: 'preview-check', 'aria-hidden': 'true' }, '□'), el('span', null, String(item))])
      ))
      : el('p', { class: 'preview-muted' }, t('preview.noAcceptance'));
    return el('section', { class: 'preview-panel preview-acceptance' }, [
      el('div', { class: 'preview-panel-heading' }, [
        el('h2', null, t('preview.acceptance')),
        el('span', { class: `preview-gate ${gateReady ? 'ready' : 'pending'}` }, gateState),
      ]),
      list,
      el('div', { class: 'preview-release-ticket' }, [
        el('span', null, t('preview.releaseTicket')),
        el('strong', null, previewValue(gate.ticket)),
      ]),
      gate.reason ? el('p', { class: 'preview-gate-reason' }, gate.reason) : null,
    ]);
  }

  function buildPreviewLogs(data, body) {
    const logs = Array.isArray(data.logs) ? data.logs : [];
    const refresh = el('button', {
      class: 'btn btn-sm',
      type: 'button',
      onClick: async (event) => {
        event.currentTarget.disabled = true;
        await refreshPreviewPage(body, true);
      },
    }, t('preview.refreshLogs'));
    const output = el('div', {
      class: 'preview-log-output',
      role: 'log',
      'aria-live': 'polite',
      'aria-label': t('preview.liveLogs'),
      tabindex: '0',
    }, logs.length
      ? logs.map((entry) => {
        const record = typeof entry === 'string' ? { line: entry } : (entry || {});
        return el('div', { class: `preview-log-line ${record.stream === 'stderr' ? 'stderr' : ''}` }, [
          el('span', { class: 'preview-log-stream' }, record.stream || 'out'),
          el('span', null, previewValue(record.line)),
        ]);
      })
      : el('div', { class: 'preview-log-empty' }, t('preview.noLogs')));
    requestAnimationFrame(() => { output.scrollTop = output.scrollHeight; });
    return el('section', { class: 'preview-panel preview-logs' }, [
      el('div', { class: 'preview-panel-heading' }, [el('h2', null, t('preview.liveLogs')), refresh]),
      output,
    ]);
  }

  function paintPreviewPage(body, data) {
    if (!body.isConnected || state.route !== 'preview') return;
    cancelPreviewPoll();
    clearNode(body);
    const phase = previewPhase(data);
    const header = el('section', { class: 'preview-command-deck' }, [
      el('div', { class: 'preview-command-copy' }, [
        el('span', { class: 'preview-eyebrow' }, t('preview.eyebrow')),
        el('div', { class: 'preview-title-row' }, [
          el('h1', null, t('preview.title')),
          el('span', { class: `preview-status ${phase}`, role: 'status', 'aria-live': 'polite' }, previewPhaseLabel(phase)),
        ]),
        el('p', null, t('preview.subtitle')),
      ]),
      buildPreviewActions(data, body),
    ]);
    const notice = buildPreviewNotice(data);
    const metrics = el('dl', { class: 'preview-metrics' }, [
      buildPreviewMetric(t('preview.readiness'), data.ready ? t('preview.ready') : t('preview.notReadyValue'), data.ready ? 'good' : 'waiting'),
      buildPreviewMetric(t('preview.health'), data.healthy ? t('preview.healthy') : t('preview.unhealthy'), data.healthy ? 'good' : 'waiting'),
      buildPreviewMetric(t('preview.targetSha'), data.target_sha),
      buildPreviewMetric(t('preview.targetBranch'), data.target_branch),
      buildPreviewMetric(t('preview.url'), data.url),
      buildPreviewMetric(t('preview.process'), data.pid ? `PID ${data.pid}${data.port ? ` · :${data.port}` : ''}` : '—'),
    ]);
    const lower = el('div', { class: 'preview-lower-grid' }, [buildPreviewAcceptance(data), buildPreviewLogs(data, body)]);
    body.appendChild(header);
    if (notice) body.appendChild(notice);
    body.appendChild(metrics);
    body.appendChild(buildPreviewStage(data));
    body.appendChild(lower);
    previewPollTimer = setTimeout(() => refreshPreviewPage(body, false), 3000);
  }

  async function refreshPreviewPage(body, announce) {
    if (!body.isConnected || state.route !== 'preview') return;
    try {
      const data = await api.getPreview();
      paintPreviewPage(body, data || {});
      if (announce) showToast(t('preview.refreshed'), 'success');
    } catch (err) {
      if (!body.isConnected || state.route !== 'preview') return;
      cancelPreviewPoll();
      clearNode(body);
      body.appendChild(el('div', { class: 'preview-load-error', role: 'alert' }, [
        el('strong', null, t('preview.unavailable')),
        el('span', null, err.message),
        el('button', { class: 'btn', type: 'button', onClick: () => refreshPreviewPage(body, false) }, t('common.refresh')),
      ]));
      previewPollTimer = setTimeout(() => refreshPreviewPage(body, false), 5000);
    }
  }

  function renderPreviewPage(container) {
    const page = el('div', { class: 'page page-preview' });
    const body = el('div', { class: 'preview-body' }, [
      el('div', { class: 'preview-loading', role: 'status' }, [buildSkeletonBlock(), el('span', null, t('preview.loading'))]),
    ]);
    page.appendChild(body);
    container.appendChild(page);
    refreshPreviewPage(body, false);
  }

  // ------------------------------------------------------------------
  // Page: Settings
  // ------------------------------------------------------------------

  function buildBranchSelect(current) {
    const options = [el('option', { value: '', selected: !current }, t('git.currentBranch'))];
    for (const branch of state.branches) options.push(el('option', { value: branch, selected: branch === current }, branch));
    return el('select', { class: 'select' }, options);
  }

  async function saveBranchPolicy(payload) {
    try {
      await api.putBranchPolicy(payload);
      showToast(t('workflow.branchPolicySaved'), 'success');
      return true;
    } catch (err) {
      showToast(err.message, 'error');
      return false;
    }
  }

  function bindBranchPolicyAutosave(select, key) {
    let savedValue = select.value;
    select.addEventListener('change', async () => {
      const nextValue = select.value;
      select.disabled = true;
      const saved = await saveBranchPolicy({ [key]: nextValue });
      if (saved) savedValue = nextValue;
      else select.value = savedValue;
      select.disabled = false;
    });
  }

  function buildBranchPolicyCard(wf) {
    const featureSelect = buildBranchSelect(wf.agent.feature_base_branch);
    const targetSelect = buildBranchSelect(wf.agent.auto_merge_target_branch);
    bindBranchPolicyAutosave(featureSelect, 'feature_base_branch');
    bindBranchPolicyAutosave(targetSelect, 'auto_merge_target_branch');
    return el('div', { class: 'card-panel settings-card' }, [
      settingsCardHeader(t('workflow.branchPolicy'), t('settings.branchPolicyDescription')),
      fieldRow([field(t('workflow.featureBaseBranch'), featureSelect), field(t('workflow.mergeTargetBranch'), targetSelect)]),
      el('div', { class: 'form-hint settings-auto-save' }, t('settings.savedAutomatically')),
    ]);
  }

  function ciStatusView(ci, status) {
    if (!ci || !ci.enabled) return { label: t('common.disabled'), className: 'muted' };
    if (status && status.in_flight) return { label: t('common.running'), className: 'active' };
    const reason = status && status.skipped_reason;
    if (reason === 'board_busy') return { label: t('board.busy'), className: 'waiting' };
    if (reason === 'lease_held') return { label: t('issue.leaseHeld'), className: 'waiting' };
    if (reason === 'max_turns_reached') return { label: t('issue.turnBudgetExhausted'), className: 'failed' };
    if (status && status.last_result === 'failed') return { label: t('common.failed'), className: 'failed' };
    if (status && status.last_result === 'not_proven') return { label: t('common.notProven'), className: 'failed' };
    if (status && (status.last_result === 'passed' || status.last_result === 'succeeded')) return { label: t('common.completed'), className: 'ok' };
    return { label: t('common.waiting'), className: 'waiting' };
  }

  function ciAgentOptions(wf, current) {
    const kinds = (state.board && state.board.board.agent_kinds) || wf.agent_kinds || [];
    const defaultLabel = t('settings.workflowDefault', { kind: (wf.agent && wf.agent.kind) || t('board.defaultAgent') });
    const options = [el('option', { value: '', selected: !current }, defaultLabel)];
    for (const kind of kinds) options.push(el('option', { value: kind, selected: kind === current }, kind));
    return options;
  }

  // Improvement modes: one opt-in checkbox each. An empty selection keeps the
  // original readiness-only heartbeat, which is what `enabled` alone meant
  // before modes existed.
  function ciModeCheckboxes(ci) {
    const supported = ci.supported_modes || ['readiness'];
    const selected = new Set(ci.modes || []);
    return supported.map((mode) =>
      el('label', { class: 'form-check', 'data-ci-mode': mode }, [
        el('input', { type: 'checkbox', 'data-ci-mode-input': mode, checked: selected.has(mode) }),
        el('span', null, t(`workflow.ciMode.${mode}`)),
      ])
    );
  }

  function settingsSectionHeading(title, description) {
    return el('div', { class: 'settings-section-heading' }, [
      el('h2', { class: 'settings-section-kicker' }, title),
      el('p', null, description),
    ]);
  }

  function settingsCardHeader(title, description, trailing) {
    const children = [
      el('div', { class: 'settings-card-header-copy' }, [
        el('h3', null, title),
        description ? el('p', null, description) : null,
      ].filter(Boolean)),
    ];
    if (trailing) children.push(trailing);
    return el('div', { class: 'settings-card-header' }, children);
  }

  function buildContinuousImprovementCard(wf, ciStatus) {
    const ci = wf.continuous_improvement || {};
    const status = ciStatus || {};
    const statusView = ciStatusView(ci, status);
    const modeChecks = ciModeCheckboxes(ci);
    const enabledInput = el('input', { id: 'ci-enabled-toggle', type: 'checkbox', checked: Boolean(ci.enabled) });
    const intervalInput = el('input', { id: 'ci-interval-input', class: 'input', type: 'number', min: '60000', step: '60000', value: ci.interval_ms || 1800000 });
    const maxTurnsInput = el('input', { id: 'ci-max-turns-input', class: 'input', type: 'number', min: '0', step: '1', value: ci.max_turns == null ? 48 : ci.max_turns });
    const agentSelect = el('select', { id: 'ci-agent-kind-select', class: 'select' }, ciAgentOptions(wf, ci.agent_kind || ''));
    const resetButton = el('button', {
      id: 'ci-reset-turns',
      class: 'btn btn-ghost',
      onClick: async (e) => {
        e.target.disabled = true;
        try {
          const result = await api.resetContinuousImprovementTurns();
          showToast(t('workflow.turnsReset'), 'success');
          renderRoute();
          return result;
        } catch (err) {
          showToast(err.message, 'error');
          return null;
        } finally {
          e.target.disabled = false;
        }
      },
    }, t('workflow.resetTurns'));
    const saveButton = el('button', {
      class: 'btn btn-primary',
      onClick: async (e) => {
        e.target.disabled = true;
        try {
          const payload = {
            enabled: enabledInput.checked,
            interval_ms: Number(intervalInput.value),
            max_turns: Number(maxTurnsInput.value),
            agent_kind: agentSelect.value,
            modes: modeChecks
              .filter((node) => node.querySelector('input').checked)
              .map((node) => node.getAttribute('data-ci-mode')),
          };
          const result = await api.putContinuousImprovement(payload);
          state.workflow = { ...wf, continuous_improvement: result.continuous_improvement };
          showToast(t('workflow.continuousImprovementSaved'), 'success');
          renderRoute();
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          e.target.disabled = false;
        }
      },
    }, t('common.save'));
    return el('div', { class: 'card-panel settings-card settings-card--featured ci-card' }, [
      settingsCardHeader(
        t('workflow.continuousImprovement'),
        t('settings.continuousImprovementDescription'),
        el('span', { class: `ci-status-pill ${statusView.className}` }, statusView.label)
      ),
      fieldRow([
        field(t('common.enabled'), el('span', { class: 'switch' }, [enabledInput, el('span', { class: 'switch-slider' })])),
        field(t('workflow.intervalMs'), intervalInput),
      ]),
      fieldRow([
        field(t('chat.maxTurns'), maxTurnsInput),
        field(t('issue.ticketAgent'), agentSelect),
      ]),
      field(t('workflow.ciModes'), el('div', { class: 'ci-modes' }, modeChecks)),
      el('div', { class: 'form-hint' }, t('workflow.ciModesHint')),
      el('div', { class: 'ci-status-grid' }, [
        kv(t('workflow.turnsUsed'), `${status.turns_used == null ? 0 : status.turns_used} / ${ci.max_turns === 0 ? t('workflow.unlimited') : ci.max_turns}`),
        kv(t('common.phase'), status.current_phase || '—'),
        kv(t('issue.lastResult'), status.last_result || '—'),
        kv(t('issue.skippedReason'), status.skipped_reason || '—'),
        kv(t('stats.ticketsCreated'), String(status.tickets_created || 0)),
        kv(t('workflow.nextDue'), status.next_due_at || '—'),
      ]),
      el('div', { class: 'ci-actions' }, [saveButton, resetButton]),
    ]);
  }

  function buildStageContractsRow(wf) {
    // F-06: `agent.stage_contracts: auto` silently switches the mechanical
    // evidence floor off as soon as a default lane is renamed. Say so here.
    const agent = (wf && wf.agent) || {};
    const enabled = agent.stage_contracts_enabled !== false;
    const lanes = 'Todo, In Progress, Verify, Document';
    return el('div', { class: enabled ? 'form-hint' : 'form-hint form-hint-warn' }, [
      el('strong', null, t('settings.stageContracts') + ': '),
      enabled ? t('settings.stageContractsOn') : t('settings.stageContractsOff', { lanes }),
    ]);
  }

  function buildLanePresetCard(presets, wf) {
    const select = el(
      'select',
      { class: 'select' },
      presets.presets.map((p) =>
        el('option', { value: p.name, selected: p.name === presets.current }, p.label)
      )
    );
    const applyButton = el('button', {
      class: 'btn btn-primary',
      onClick: async (e) => {
        e.target.disabled = true;
        try {
          const result = await api.applyLanePreset(select.value);
          showToast(t('settings.lanePresetApplied', { name: result.applied }), 'success');
          renderRoute();
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          e.target.disabled = false;
        }
      },
    }, t('common.apply'));
    return el('div', { class: 'card-panel settings-card' }, [
      settingsCardHeader(t('settings.lanePreset'), t('settings.lanePresetDescription')),
      fieldRow([field(t('settings.lanePresetChoose'), select)]),
      el('div', { class: 'form-hint' }, t('settings.lanePresetHint')),
      buildStageContractsRow(wf),
      el('div', { class: 'settings-card-actions' }, [applyButton]),
    ]);
  }

  function buildBoardInfoCard(wf) {
    return el('div', { class: 'card-panel settings-card settings-card--utility' }, [
      settingsCardHeader(t('settings.boardInfo'), t('settings.boardInfoDescription')),
      el('div', { class: 'kv-grid' }, [
        kv(t('settings.workflowPath'), wf.workflow_path),
        kv(t('settings.defaultAgent'), (wf.agent && wf.agent.kind) || '—'),
        kv(t('settings.trackerKind'), state.board ? state.board.board.tracker_kind : '—'),
        kv(t('settings.pollingInterval'), t('settings.milliseconds', { n: wf.polling_interval_ms })),
      ]),
      el('a', { href: '/api/v1/state', target: '_blank', rel: 'noopener', class: 'link' }, t('settings.viewRawApiState')),
    ]);
  }

  function buildInterfaceCard() {
    const select = el(
      'select',
      { class: 'select', onChange: (e) => window.i18n.setLang(e.target.value) },
      window.i18n.languages.map((lang) =>
        el('option', { value: lang.code, selected: lang.code === window.i18n.lang }, lang.label)
      )
    );
    return el('div', { class: 'card-panel settings-card settings-card--utility' }, [
      settingsCardHeader(t('settings.interface'), t('settings.interfaceDescription')),
      field(t('settings.language'), select),
      el('div', { class: 'form-hint' }, t('settings.languageHint')),
    ]);
  }

  function buildRefreshCard() {
    const btn = el('button', {
      class: 'btn btn-primary',
      onClick: async (e) => {
        e.target.disabled = true;
        try {
          await api.refresh();
          showToast(t('board.refreshRequested'), 'success');
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          e.target.disabled = false;
        }
      },
    }, t('board.refreshOrchestrator'));
    return el('div', { class: 'card-panel settings-card settings-card--utility settings-card--control' }, [
      settingsCardHeader(t('settings.manualControls'), t('settings.manualControlsDescription')),
      el('div', { class: 'settings-card-actions' }, [btn]),
    ]);
  }

  async function renderSettingsPage(container) {
    const page = el('div', { class: 'page page-settings' });
    page.appendChild(el('div', { class: 'topbar settings-topbar' }, [
      el('div', { class: 'settings-title-group' }, [
        el('h1', { class: 'page-title' }, t('nav.settings')),
        el('p', null, t('settings.pageDescription')),
      ]),
      el('span', { class: 'settings-config-badge' }, 'WORKFLOW.md'),
    ]));
    const body = el('div', { class: 'settings-body' }, [buildSkeletonBlock()]);
    page.appendChild(body);
    container.appendChild(page);
    try {
      const [wf, branchesResp, board] = await Promise.all([
        api.getWorkflow(),
        api.getBranches(),
        state.board ? Promise.resolve(state.board) : api.getBoard(),
      ]);
      const ciStatus = await api.getContinuousImprovementStatus();
      const lanePresets = await api.getLanePresets();
      state.workflow = wf;
      state.branches = branchesResp.branches;
      if (!state.board) state.board = board;
      clearNode(body);
      body.appendChild(settingsSectionHeading(t('settings.workspace'), t('settings.workspaceDescription')));
      body.appendChild(buildBoardInfoCard(wf));
      body.appendChild(buildInterfaceCard());
      body.appendChild(buildRefreshCard());
      body.appendChild(settingsSectionHeading(t('settings.workflowSetup'), t('settings.workflowSetupDescription')));
      body.appendChild(buildLanePresetCard(lanePresets, wf));
      body.appendChild(buildBranchPolicyCard(wf));
      body.appendChild(settingsSectionHeading(t('settings.automation'), t('settings.automationDescription')));
      body.appendChild(buildContinuousImprovementCard(wf, ciStatus));
    } catch (err) {
      clearNode(body);
      body.appendChild(el('div', { class: 'empty-state' }, t('settings.loadFailed', { error: err.message })));
      // The language picker is client-side only, so keep it reachable even
      // when the orchestrator is down.
      body.appendChild(settingsSectionHeading(t('settings.workspace'), t('settings.workspaceDescription')));
      body.appendChild(buildInterfaceCard());
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

  // Hold DOM updates while the user is mid-edit (overlay input focused) or
  // mid-drag — a re-render would remove the drag-source node, which silently
  // cancels an HTML5 drag. Shared with the run execution panel's poll.
  function shouldHoldRender() {
    return isEditingFocused() || Boolean(document.querySelector('.card.dragging'));
  }

  async function pollBoard() {
    // The fetch itself still runs while held, so the connection indicator
    // stays truthful.
    const holdRender = shouldHoldRender();
    try {
      const board = await api.getBoard();
      state.connected = true;
      state.lastSuccessfulPollAt = Date.now();
      if (!holdRender) {
        const firstLoad = !state.board;
        state.board = board;
        const nameEl = document.getElementById('board-name');
        if (nameEl) {
          nameEl.textContent = board.board.name || 'symphony';
          // The board name is data, not copy — drop the placeholder binding so
          // a later language switch does not reset it to "Loading…".
          nameEl.removeAttribute('data-i18n');
        }
        if (state.route === 'board') {
          if (firstLoad || !document.getElementById('board-scroll')) renderRoute();
          else if (state.boardView === 'lanes') renderBoardColumns(document.getElementById('board-scroll'));
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
    // Static markup in index.html is translated once here, then again on every
    // language change; the SPA views are rebuilt wholesale by renderRoute().
    window.i18n.applyStaticNodes();
    window.i18n.onChange(() => {
      renderRoute();
      updateConnectionIndicator();
    });
    wireGlobalShortcuts();
    const selector = document.getElementById('project-selector');
    if (selector) selector.addEventListener('change', () => switchProject(selector.value));
    const manageButton = document.getElementById('manage-projects');
    if (manageButton) manageButton.addEventListener('click', openManageProjectsDialog);
    handleRouteChange();
    loadProjects();
    pollBoard();
  }

  document.addEventListener('DOMContentLoaded', boot);

  // navigate() is reachable from the console for debugging; keep it referenced
  // so linters don't flag it as unused if a future page wires it up directly.
  void navigate;
})();
