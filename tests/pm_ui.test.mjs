import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import vm from 'node:vm';

import { applyUnifiedDiff, capabilityAvailability, chooseAvailableChange, directHistoryChangeId, mountArtifacts, moveOperations, parseTsv, pmText, serializeWbsTsv, withProjectIdentity } from '../packages/pm/src/symphony/pm/static/index.js';
import { createPmApi } from '../packages/pm/src/symphony/pm/static/api.js';
import { diagramSummary, diagramWindows } from '../packages/pm/src/symphony/pm/static/diagrams.js';
import { exchangeControlState, validateWorkbookFilename } from '../packages/pm/src/symphony/pm/static/exchange.js';
import { sourceEditPolicy } from '../packages/pm/src/symphony/pm/static/documents.js';
import { storyboardPatchOperation } from '../packages/pm/src/symphony/pm/static/storyboard.js';
import { createPmOverlay } from '../packages/pm/src/symphony/pm/static/overlay.js';
import { planningActionRequest, verificationActions } from '../packages/pm/src/symphony/pm/static/assistance.js';
import { analyzeSafeDiagram } from '../packages/pm/scripts/pm_diagram_entry.mjs';
// Namespace imports keep a missing round9 export a per-test failure instead of a module link error.
import * as pmDocumentsModule from '../packages/pm/src/symphony/pm/static/documents.js';
import * as pmIndexModule from '../packages/pm/src/symphony/pm/static/index.js';

test('quoted TSV preserves tabs, quotes, and multiline values', () => {
  assert.deepEqual(parseTsv('"T-1"\t"notes"\t"first\nsecond\t""quoted"""\n'), [
    ['T-1', 'notes', 'first\nsecond\t"quoted"'],
  ]);
});

test('copied WBS uses the same task-field-value grammar accepted by paste', () => {
  const rows = parseTsv(serializeWbsTsv([{ task_id: 'T-1', parent_id: null, order: 10, kind: 'Feature', title: 'A', acceptance: ['One'], verification: [], dependencies: [], requirement_refs: [], storyboard_refs: [], design_refs: [] }]));
  assert.deepEqual(rows[0], ['Task ID', 'Field', 'Value']);
  assert.ok(rows.some((row) => row[0] === 'T-1' && row[1] === 'title' && row[2] === 'A'));
  assert.ok(rows.some((row) => row[0] === 'T-1' && row[1] === 'acceptance' && row[2] === '["One"]'));
});

test('unified proposal applies only when context matches', () => {
  const patch = '@@ -1,2 +1,2 @@\n one\n-two\n+three';
  assert.equal(applyUnifiedDiff('one\ntwo', patch), 'one\nthree');
  assert.throws(() => applyUnifiedDiff('changed\ntwo', patch), /no longer matches/);
});

test('move undo restores both original sibling orders', () => {
  const tasks = [{ task_id: 'T-1', parent_id: null, order: 10 }, { task_id: 'T-2', parent_id: null, order: 20 }];
  const move = moveOperations(tasks, 'T-2', 'up');
  assert.deepEqual(move.operations.map((item) => [item.task_id, item.value]), [['T-2', 10], ['T-1', 20]]);
  assert.deepEqual(move.inverse.map((item) => [item.task_id, item.value]), [['T-2', 20], ['T-1', 10]]);
});

test('semantic creation uses the authenticated document mutation envelope', async () => {
  let captured;
  const api = createPmApi({ request: async (path, options) => { captured = { path, options }; return { impacted_ids: ['SB-12345678'] }; } }, 'p1');
  const result = await api.createSemantic('change1', 'revision1', 'ux', 'storyboard', 'Checkout');
  assert.equal(result.impacted_ids[0], 'SB-12345678');
  assert.equal(captured.path, '/changes/change1/documents/ux');
  assert.equal(captured.options.method, 'PATCH');
  assert.deepEqual(captured.options.body.payload.operations, [{ op: 'create_semantic', kind: 'storyboard', title: 'Checkout' }]);
  assert.equal(captured.options.body.project_id, 'p1');
  assert.equal(captured.options.body.expected_revision, 'revision1');
});

test('refresh drops an archived selection and chooses only a remaining change', () => {
  const changes = [{ change_id: 'remaining' }];
  assert.equal(chooseAvailableChange(changes, 'archived', 'archived').change_id, 'remaining');
  assert.equal(chooseAvailableChange([], 'archived', 'archived'), null);
});

test('an original archived deep link is loaded directly outside the active list', () => {
  const changes = [{ change_id: 'remaining' }];
  assert.equal(directHistoryChangeId(changes, 'archived'), 'archived');
  assert.equal(directHistoryChangeId(changes, 'remaining'), '');
  assert.equal(directHistoryChangeId(changes, ''), '');
});

test('capability availability distinguishes requested and effective actions', () => {
  const payload = {
    flags: { authoring: true }, effective_flags: { authoring: false },
    capabilities: { authoring: { requested: true, effective: false, reasons: ['Requires G0 and G1'], development_verification: false } },
  };
  assert.deepEqual(capabilityAvailability(payload, 'authoring'), {
    requested: true, effective: false, reasons: ['Requires G0 and G1'], developmentVerification: false,
  });
  assert.deepEqual(capabilityAvailability({ flags: { exchange: true } }, 'exchange'), {
    requested: true, effective: true, reasons: [], developmentVerification: false,
  });
});

test('missing workbook dependencies leave readable exports available', () => {
  const unavailable = { effective: false };
  assert.deepEqual(exchangeControlState('xlsx', unavailable), {
    formatDisabled: false, workbookDisabled: true, exportDisabled: true,
  });
  for (const format of ['csv', 'markdown', 'html', 'zip']) {
    assert.equal(exchangeControlState(format, unavailable).exportDisabled, false, format);
  }
  assert.deepEqual(exchangeControlState('markdown', { effective: true }, true), {
    formatDisabled: false, workbookDisabled: true, exportDisabled: false,
  });
  assert.equal(exchangeControlState('xlsx', { effective: true }, true).exportDisabled, false);
});

test('workbook filenames preserve an exact plain XLSX basename', async () => {
  assert.equal(validateWorkbookFilename('Planning Review.XLSX'), 'Planning Review.XLSX');
  for (const filename of ['', '.xlsx', ' review.xlsx', 'review.xlsx ', 'folder/review.xlsx', 'folder\\review.xlsx', 'review.xlsm', 'review.xlsx\0ignored']) {
    assert.throws(() => validateWorkbookFilename(filename), /XLSX|plain filename/, filename);
  }

  const calls = [];
  const api = createPmApi({ request: async (path, options = {}) => { calls.push({ path, options }); return {}; } }, 'project-a');
  await api.importPreview('change-a', 'rev-a', 'Planning Review.XLSX', 'eHl6');
  assert.equal(calls[0].options.body.payload.filename, 'Planning Review.XLSX');
  assert.throws(() => api.importPreview('change-a', 'rev-a', '../review.xlsx', 'eHl6'), /plain filename/);
  assert.equal(calls.length, 1);
});

test('Korean PM text covers dynamic import conflicts and approval', () => {
  assert.equal(pmText('{count} proposed changes', { count: 3 }, 'ko'), '제안된 변경 3개');
  assert.equal(pmText('{count} conflicts require resolution', { count: 1 }, 'ko'), '해결이 필요한 충돌 1개');
  assert.equal(pmText('Resolve {key}', { key: 'TASK-1:title' }, 'ko'), 'TASK-1:title 해결');
  assert.equal(pmText('Apply reviewed import', {}, 'ko'), '검토한 가져오기 적용');
  assert.equal(pmText('Approve this exact planning revision locally?', {}, 'ko'), '이 정확한 기획 리비전을 로컬에서 승인하시겠습니까?');
});

test('diagram preflight accepts supported syntax before parser analysis', () => {
  const flow = diagramSummary('flowchart LR\n A[Long human readable label] --> B[Another label]');
  assert.equal(flow.allowed, true);
  assert.equal(flow.text, 'Diagram preview');
  const erd = diagramSummary('erDiagram\n USER ||--o{ ORDER : places\n USER {\n string display_name\n }');
  assert.equal(erd.allowed, true);
  assert.equal(erd.text, 'Diagram preview');
});

test('Mermaid parsed model enforces dotted chains and parallel edge caps', async () => {
  const dotted = `flowchart LR;${Array.from({ length: 101 }, (_, index) => `N${index}-->N${index + 1}`).join(';')}`;
  await assert.rejects(() => analyzeSafeDiagram(dotted), /102 nodes, 101 edges/);
  const parallel = `flowchart LR;${Array.from({ length: 20 }, (_, source) => Array.from({ length: 11 }, (_, target) => `S${source}-->T${target}`).join(';')).join(';')}`;
  await assert.rejects(() => analyzeSafeDiagram(parallel), /31 nodes, 220 edges/);
  assert.deepEqual(await analyzeSafeDiagram('flowchart LR;A-->B;B-->C'), { nodes: 3, edges: 2 });
});

test('storyboard field edits use the stable server identity and separate transitions', () => {
  const operation = storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: false }, {
    title: 'Review', screen_route: '/review', actor_goal: 'Review invitation', initial_state: 'Pending',
    ui: 'Card', user_action: 'Approve', system_response: 'Persist', success: 'Approved', alternate: 'Already approved', failure: 'Denied',
    requirement_refs: ['REQ-12345678'], design_refs: ['API-12345678'],
  });
  assert.equal(operation.storyboard_id, 'SB-12345678');
  assert.deepEqual(operation.fields.success, 'Approved');
  assert.deepEqual(operation.fields.alternate, 'Already approved');
  assert.deepEqual(operation.fields.failure, 'Denied');
});

test('legacy storyboard conversion refuses partial transition replacement', () => {
  assert.throws(() => storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: true }, { ui: 'Card' }), /all three/);
  const operation = storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: true }, {
    ui: 'Card', user_action: 'Approve', system_response: 'Persist',
    success: 'Approved', alternate: 'Already approved', failure: 'Denied',
  });
  assert.equal(operation.fields.system_response, 'Persist');
});

test('all seven assistance actions retain server-scoped selections', () => {
  const actionIds = ['draft_artifact', 'refine_section', 'find_gaps', 'review_consistency', 'explain_dependency', 'propose_wbs', 'update_impacted_artifacts'];
  for (const action of actionIds) {
    const request = planningActionRequest(action, { documentId: action === 'propose_wbs' ? 'tasks' : 'ux', sectionId: 'ux:states', dependency: { task_id: 'T-2', predecessor_id: 'T-1' }, impactedDocumentIds: ['ux', 'design'], instruction: 'Bounded request' });
    assert.equal(request.action, action);
    assert.ok(Array.isArray(request.impacted_document_ids));
  }
});

test('assistance scope validation rejects incomplete or stale hidden selections', () => {
  assert.throws(() => planningActionRequest('refine_section', { documentId: 'ux' }), /section/);
  assert.throws(() => planningActionRequest('explain_dependency', { documentId: 'tasks' }), /dependency/);
  assert.throws(() => planningActionRequest('review_consistency', { documentId: 'ux', impactedDocumentIds: ['ux'] }), /at least two/);
  assert.throws(() => planningActionRequest('update_impacted_artifacts', { documentId: 'ux' }), /at least one/);
  assert.deepEqual(planningActionRequest('draft_artifact', { documentId: 'ux', sectionId: 'stale', dependency: { task_id: 'T-2', predecessor_id: 'T-1' }, impactedDocumentIds: ['design'] }), {
    action: 'draft_artifact', document_id: 'ux', section_id: null, dependency: null, instruction: '', impacted_document_ids: ['ux'],
  });
});

test('zero-run evidence exposes native and manual actions without cross-kind fields', () => {
  const actions = verificationActions([
    { procedure_id: 'focused', kind: 'native', definition_digest: 'sha256:n' },
    { procedure_id: 'manual-ux', kind: 'manual', definition_digest: 'sha256:m' },
  ]);
  assert.deepEqual(actions.map((item) => [item.kind, item.label]), [
    ['native', 'Run verification'], ['manual', 'Submit completion evidence'],
  ]);
  assert.equal(actions[0].acceptsReportedResult, false);
  assert.equal(actions[1].acceptsReportedResult, true);
});

test('source editor refuses newline dialects a textarea would normalize', () => {
  assert.deepEqual(sourceEditPolicy('one\ntwo\n'), { editable: true, reason: '' });
  assert.equal(sourceEditPolicy('one\r\ntwo\r\n').editable, false);
  assert.equal(sourceEditPolicy('one\r\ntwo\n').editable, false);
});

test('PM API uses typed storyboard, assistance, evidence, coverage, and waiver contracts', async () => {
  const calls = [];
  const api = createPmApi({ request: async (path, options = {}) => { calls.push({ path, options }); return { ok: true }; } }, 'project-a');
  await api.patchStoryboard('change-a', 'rev-a', { op: 'set_storyboard_fields', storyboard_id: 'SB-1', fields: { title: 'A' } });
  api.startPlanning('change-a', 'rev-a', planningActionRequest('find_gaps', { documentId: 'ux', impactedDocumentIds: ['ux'] }));
  await api.evidencePreview('change-a', 'rev-a', { ticket_identifier: 'PM-1', procedure_id: 'manual-ux' });
  await api.coverage('change-a', 'rev-a');
  await api.waiverPreview('change-a', 'rev-a', 'SC-1', 'Reason', 'Reviewer');
  await api.importPreview('change-a', 'rev-a', 'review.xlsx', 'eHl6');
  assert.equal(calls[0].options.body.payload.operations[0].storyboard_id, 'SB-1');
  assert.equal(calls[1].options.body.action, 'find_gaps');
  assert.equal(calls[2].options.body.project_id, 'project-a');
  assert.match(calls[3].path, /revision=rev-a/);
  assert.equal(calls[4].options.body.semantic_id, 'SC-1');
  assert.deepEqual(calls[5].options.body.payload, { filename: 'review.xlsx', base64: 'eHl6' });
});

test('absolute PM evidence URLs retain one API prefix and stable project identity', () => {
  assert.equal(withProjectIdentity('/api/v1/pm/evidence/r1/report', 'project-a'), '/api/v1/pm/evidence/r1/report?project_id=project-a');
  assert.equal(withProjectIdentity('/api/v1/pm/evidence/r1/report?project_id=project-a', 'project-a'), '/api/v1/pm/evidence/r1/report?project_id=project-a');
});

test('an explicit pmView deep link wins over the remembered subview once', async () => {
  const store = new Map();
  globalThis.sessionStorage = { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, String(value)) };
  globalThis.history = { replaceState: () => {} };
  globalThis.location = { href: 'http://127.0.0.1/?pmChange=c1&pmTab=design&pmView=architecture#/artifacts' };
  store.set('symphony.pm.p1.c1.design', 'storyboard');
  const { createPmState } = await import('../packages/pm/src/symphony/pm/static/state.js');
  const state = createPmState(() => {}, 'p1');
  state.change = { change_id: 'c1' };
  state.restoreViewState();
  assert.equal(state.designView, 'architecture');
  assert.equal(store.get('symphony.pm.p1.c1.design'), 'architecture');
  state.setDesignView('erd');
  state.restoreViewState();
  assert.equal(state.designView, 'erd');
});

test('over-limit diagrams expand on demand in parser-checked parts of at most 100 nodes', async () => {
  const chain = (nodes) => 'flowchart LR\n' + Array.from({ length: nodes - 1 }, (_, i) => `N${i}-->N${i + 1}`).join('\n');
  await assert.rejects(analyzeSafeDiagram(chain(101)), /101 nodes/);
  for (const [nodes, parts] of [[101, 2], [250, 3]]) {
    const windows = diagramWindows(chain(nodes));
    assert.equal(windows.total, nodes);
    assert.equal(windows.count, parts);
    let covered = 0;
    for (let index = 0; index < windows.count; index += 1) {
      const part = windows.window(index);
      const model = await analyzeSafeDiagram(part.source);
      assert.ok(model.nodes <= 100 && model.edges <= 200);
      assert.equal(model.nodes, part.last - part.first + 1);
      covered += model.nodes;
    }
    assert.equal(covered, nodes);
  }
  const er = 'erDiagram\n' + Array.from({ length: 120 }, (_, i) => `E${i} ||--o{ E${i + 1} : has`).join('\n');
  const erWindows = diagramWindows(er);
  assert.equal(erWindows.count, 2);
  assert.deepEqual(await analyzeSafeDiagram(erWindows.window(0).source), { nodes: 100, edges: 99 });
});

test('diagram parts keep quoted semicolons, cross-part labels and the edge cap', async () => {
  const quoted = 'flowchart LR\nN0["Start; reviewed"]\n' + Array.from({ length: 100 }, (_, i) => `N${i}-->N${i + 1}`).join('\n');
  const quotedParts = diagramWindows(quoted);
  assert.equal(quotedParts.count, 2);
  // Labelled nodes need a browser DOM inside Mermaid; the installed browser
  // benchmark parses these exact parts. Here the split itself is checked.
  assert.match(quotedParts.window(0).source, /N0\["Start; reviewed"\]-->N1|N0\["Start; reviewed"\]/);
  assert.ok(!/N0\["Start$/m.test(quotedParts.window(0).source));

  const declared = Array.from({ length: 100 }, (_, i) => `n${i}["Node ${i}"]`).join('\n');
  const crossing = `flowchart TD\n${declared}\nn99 --> n100["Important Korean 결제 승인"]`;
  const crossingParts = diagramWindows(crossing);
  assert.equal(crossingParts.count, 2);
  const second = crossingParts.window(1);
  assert.match(second.source, /n100\["Important Korean 결제 승인"\]/);
  assert.equal(second.first, 101);

  const dense = 'flowchart LR\n' + Array.from({ length: 21 }, (_, a) => Array.from({ length: 21 }, (_, b) => (a < b ? `M${a}-->M${b}` : null)).filter(Boolean)).flat().join('\n');
  await assert.rejects(analyzeSafeDiagram(dense), /210 edges/);
  const denseParts = diagramWindows(dense);
  assert.ok(denseParts.count >= 2);
  let nodes = 0;
  for (let index = 0; index < denseParts.count; index += 1) {
    const model = await analyzeSafeDiagram(denseParts.window(index).source);
    assert.ok(model.nodes <= 100 && model.edges <= 200);
    nodes += model.nodes;
  }
  assert.equal(nodes, 21);
});

test('a redefined node label keeps its final definition inside a diagram part', () => {
  const declared = Array.from({ length: 101 }, (_, i) => `n${i}["Node ${i}"]`).join('\n');
  const parts = diagramWindows(`flowchart TD\n${declared}\nn99 --> n100["Final Korean 결제 승인"]`);
  assert.equal(parts.count, 2);
  const lines = parts.window(1).source.split('\n');
  const labels = lines.filter((line) => line.startsWith('n100['));
  assert.equal(labels.at(-1), 'n100["Final Korean 결제 승인"]');
});

// D7 is pinned here rather than read from an unshipped planning document.
const GUIDE_LABELS = {
  workspace: ['New change', 'Repair workspace', 'Convert legacy plan', 'Edit in external editor', 'Validate', 'Approve revision'],
  idea: ['Edit source', 'Save draft', 'Planning assistant', 'Add decision'],
  requirements: ['Add capability', 'Edit requirements', 'Add requirement', 'Add scenario', 'Waive coverage', 'Revoke waiver'],
  design: ['Storyboard', 'Save storyboard', 'Add storyboard', 'Add contract'],
  plan: ['Work breakdown', 'Dependencies', 'Add group', 'Add child', 'Delete', 'Cancel task', 'Undo', 'Download export', 'Preview import', 'Preview approved handoff', 'Preview archive'],
  code: ['Run verification', 'Submit completion evidence', 'Review Git commit', 'Open run evidence', 'Accept verified revision'],
};
const GUIDE_SUMMARY = 'What do these buttons do?';
const DEMO_HEADING = 'Demo workspace';
const DEMO_SENTENCE = 'This is a disposable demo. Planning actions are unlocked only on this server; no AI worker starts and nothing leaves this computer.';
const DEMO_ASSISTANCE_REASON = 'Off in the demo: it would contact an AI model.';
const DEVELOPMENT_SENTENCE = 'Development verification is active for configured actions. Dependency checks still apply, and release gates are not marked passed.';
const NODE_REASON = 'Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)';
const CODEX_REASON = 'Codex schema probe is missing restricted app-server controls. Install a compatible Codex CLI supporting restricted app-server controls, then retry. No fallback is used. Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login.';
const ARCHIVED_REASON = 'Archived planning history is read-only.';
const PM_DIRECTORY = new URL('../packages/pm/src/symphony/pm/static/', import.meta.url);

async function guideEntries() {
  // A missing future module must fail these tests, not abort the original suite.
  const { PM_GUIDES } = await import(new URL('guide.js', PM_DIRECTORY));
  assert.ok(PM_GUIDES && typeof PM_GUIDES === 'object', 'guide.js must export PM_GUIDES (area -> label -> sentence)');
  return PM_GUIDES;
}

function withoutKoMaps(source) {
  return source.replace(/\bconst\s+KO\s*=\s*(?:new\s+Map\(Object\.entries\()?\{[\s\S]*?\}(?:\)\))?\s*;/g, '');
}

function controlLabels(source) {
  const clean = withoutKoMaps(source);
  const literals = [...clean.matchAll(/(['"])((?:\\.|(?!\1)[^\\\r\n])*)\1/g)].map((match) => match[2]);
  const labels = new Set(literals);
  // Only this actual template and its declared kind list satisfy Add <kind>.
  if (clean.includes('`Add ${kind}`')) {
    const kinds = clean.match(/\bconst\s+kinds\s*=([\s\S]*?);/)?.[1] || '';
    for (const match of kinds.matchAll(/['"](decision|storyboard|contract|requirement|scenario)['"]/g)) labels.add(`Add ${match[1]}`);
  }
  return labels;
}

test('R9 guide table pins all six areas and exactly 36 D7 labels', async () => {
  const entries = await guideEntries();
  assert.deepEqual(Object.keys(entries).sort(), Object.keys(GUIDE_LABELS).sort());
  assert.equal(Object.values(entries).reduce((count, area) => count + Object.keys(area).length, 0), 36);
  for (const [area, labels] of Object.entries(GUIDE_LABELS)) {
    assert.deepEqual(Object.keys(entries[area]).sort(), [...labels].sort(), area);
    for (const sentence of Object.values(entries[area])) {
      assert.equal(typeof sentence, 'string', area);
      assert.ok(sentence.trim().length > 0 && !/<[^>]+>/.test(sentence), `${area}: a plain explanation is required`);
    }
  }
});

test('R9 guide labels exist outside guide.js and KO maps, including Add ${kind}', async () => {
  // Counterexamples prove that translation keys and arbitrary kinds cannot pass.
  assert.equal(controlLabels("const KO = new Map(Object.entries({ 'Only in KO': '번역' }));").has('Only in KO'), false);
  assert.equal(controlLabels("const KO = { 'Only in KO': '번역' };").has('Only in KO'), false);
  assert.equal(controlLabels("const kinds = ['decision']; button(`Add ${kind}`);").has('Add decision'), true);
  assert.equal(controlLabels("const kinds = ['decision']; button(`Add ${kind}`);").has('Add scenario'), false);
  assert.equal(controlLabels("const kinds = ['scenario']; button(`Other ${kind}`);").has('Add scenario'), false);
  const files = (await readdir(PM_DIRECTORY)).filter((name) => name.endsWith('.js') && name !== 'guide.js');
  const labels = new Set((await Promise.all(files.map(async (name) => [...controlLabels(await readFile(new URL(name, PM_DIRECTORY), 'utf8'))]))).flat());
  for (const label of Object.values(GUIDE_LABELS).flat()) assert.ok(labels.has(label), `D7 label has no control literal: ${label}`);
  const entries = await guideEntries();
  for (const label of Object.values(entries).flatMap(Object.keys)) assert.ok(labels.has(label), `Guide label has no control literal: ${label}`);
});

function assertKorean(strings) {
  const missing = strings.filter((value) => pmText(value, {}, 'ko') === value || !/[가-힣]/.test(pmText(value, {}, 'ko')));
  assert.deepEqual(missing, [], 'Every guide/status string must have a Korean entry in index.js');
}

test('R9 every guide summary, label and explanation has a Korean entry', async () => {
  const entries = await guideEntries();
  assertKorean([GUIDE_SUMMARY, ...Object.values(entries).flatMap((area) => Object.entries(area).flat())]);
});

test('R9 index.js translates Save storyboard, Review Git commit, Open run evidence and Cancel task', () => {
  assertKorean(['Save storyboard', 'Review Git commit', 'Open run evidence', 'Cancel task']);
});

// Small DOM adapter for the real mountArtifacts renderer. It implements only
// construction, queries and text walking; no browser library or UI exports added.
class UiNode {
  constructor(tag = '', text = '') {
    this.tag = tag; this.nodeType = tag ? 1 : 3; this.nodeValue = text;
    this.childNodes = []; this.attributes = new Map(); this.dataset = {};
    this.disabled = false; this.title = ''; this.open = false; this.listeners = new Map();
    this.classList = {
      contains: (name) => (this.getAttribute('class') || '').split(' ').includes(name),
      add: (name) => this.setAttribute('class', `${this.getAttribute('class') || ''} ${name}`.trim()),
      remove: (name) => this.setAttribute('class', (this.getAttribute('class') || '').split(' ').filter((item) => item !== name).join(' ')),
      toggle: (name, force) => {
        const present = (this.getAttribute('class') || '').split(' ').includes(name);
        const enabled = force === undefined ? !present : force;
        this.classList[enabled ? 'add' : 'remove'](name); return enabled;
      },
    };
  }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map((node) => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(String(value)); }
  append(...nodes) {
    for (const value of nodes) {
      const node = value instanceof UiNode ? value : new UiNode('', String(value));
      node.remove(); this.childNodes.push(node); node.parentNode = this;
    }
  }
  appendChild(node) { this.append(node); return node; }
  prepend(...nodes) { const first = this.childNodes[0] || null; for (const node of nodes) this.insertBefore(node, first); }
  insertBefore(node, reference) {
    if (node === reference) return node;
    if (reference !== null && !this.childNodes.includes(reference)) throw new Error('NotFoundError');
    node.remove(); const index = reference === null ? this.childNodes.length : this.childNodes.indexOf(reference);
    this.childNodes.splice(index, 0, node); node.parentNode = this; return node;
  }
  before(node) { this.parentNode?.insertBefore(node, this); }
  after(node) { this.parentNode?.insertBefore(node, this.nextSibling); }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter((node) => node !== this); this.parentNode = null; }
  get parentElement() { return this.parentNode?.nodeType === 1 ? this.parentNode : null; }
  get firstElementChild() { return this.children[0] || null; }
  get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null; }
  get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
  get previousElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null; }
  get isConnected() { return this.tag === 'body' || !!this.parentNode?.isConnected; }
  contains(node) { return this === node || this.childNodes.some((child) => child.contains(node)); }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get open() { return this.attributes.has('open'); }
  set open(value) { if (value) this.attributes.set('open', ''); else this.attributes.delete('open'); }
  get tabIndex() { const value = this.getAttribute('tabindex'); return value === null ? (['button', 'input', 'textarea', 'select'].includes(this.tag) ? 0 : -1) : Number(value); }
  set tabIndex(value) { this.setAttribute('tabindex', value); }
  get options() { return this.querySelectorAll('option'); }
  get selectedOptions() { return this.options.filter((node) => node.selected); }
  get children() { return this.childNodes.filter((node) => node.nodeType === 1); }
  replaceChildren(...nodes) { for (const child of this.childNodes) child.parentNode = null; this.childNodes = []; this.append(...nodes); }
  setAttribute(key, value) {
    this.attributes.set(key, String(value));
    if (['open', 'disabled', 'hidden', 'selected'].includes(key)) this[key] = true;
    if (['value', 'title'].includes(key)) this[key] = String(value);
  }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  hasAttribute(key) { return this.attributes.has(key); }
  removeAttribute(key) {
    this.attributes.delete(key);
    if (['open', 'disabled', 'hidden', 'selected'].includes(key)) this[key] = false;
  }
  matches(selector) {
    if (this.nodeType !== 1) return false;
    // Whitespace inside an attribute value belongs to the same selector.
    const parts = selector.trim().match(/(?:[^\s[\]]+|\[[^\]]*\])+/g) || [];
    if (parts.length > 1) {
      const last = parts.pop(); if (!this.matches(last)) return false;
      if (parts.at(-1) === '>') { parts.pop(); return !!this.parentElement?.matches(parts.join(' ')); }
      for (let ancestor = this.parentElement; ancestor; ancestor = ancestor.parentElement) if (ancestor.matches(parts.join(' '))) return true;
      return false;
    }
    const negatives = [...selector.matchAll(/:not\(([^)]+)\)/g)].map((match) => match[1]);
    if (negatives.some((part) => this.matches(part))) return false;
    const plain = selector.replace(/:not\([^)]+\)/g, '');
    if (!/^(?:[\w-]+|\*)?(?:\.[\w-]+)*(?:\[[\w-]+(?:=["']?[^\]"']*["']?)?\])*$/.test(plain)) return false;
    const tag = plain.match(/^[\w-]+/)?.[0];
    const classes = [...plain.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
    const attrs = [...plain.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)];
    return (!tag || this.tag === tag) && classes.every((name) => this.className.split(' ').includes(name))
      && attrs.every(([, name, value]) => this.attributes.has(name) && (value === undefined || this.getAttribute(name) === value));
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    return this.childNodes.flatMap((node) => [...(selectors.some((item) => node.matches(item)) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener); this.listeners.set(type, listeners);
  }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener)); }
  async trigger(type, extra = {}) { for (const listener of this.listeners.get(type) || []) await listener({ type, target: this, preventDefault() {}, stopPropagation() {}, ...extra }); }
  focus(options) { this.focusCalls ??= []; this.focusCalls.push(options); globalThis.document.activeElement = this; }
}

function storageStub() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: (key) => values.delete(key) };
}

async function mountedPm(t, { tab = 'idea', view = '', requested = '', demo = false, language = 'en', hostTools = {}, gated = false, archived = false, development = false, demoAssistance = false, dirtyChanges = [], apiRequest = null, draft = null, announcements = [], media = null, lifecycle = null } = {}) {
  const el = (tag, attrs, children) => {
    const node = new UiNode(tag);
    for (const [key, value] of Object.entries(attrs || {})) if (value !== null && value !== undefined && value !== false) node.setAttribute(key, typeof value === 'boolean' ? '' : value);
    for (const child of (Array.isArray(children) ? children : children == null ? [] : [children])) if (child != null && child !== false) node.append(child);
    if (tag === 'select') node.value = node.options.find((item) => item.selected)?.value || node.options[0]?.value || '';
    return node;
  };
  const globals = {
    window: { i18n: { lang: language }, ...(media ? { matchMedia: () => media } : {}) },
    location: new URL(`http://127.0.0.1/?pmTab=${tab}&pmView=${view}&pmChange=${requested}#/artifacts`),
    history: { replaceState() {} }, sessionStorage: storageStub(), localStorage: storageStub(), NodeFilter: { SHOW_TEXT: 4 },
    document: {
      // WBS enhancement stays disconnected; these tests exercise native controls.
      querySelector: () => null, head: { appendChild() {} }, createElement: (tag) => el(tag), body: el('body'),
      createTreeWalker: (scope) => {
        const texts = (node) => node.nodeType === 3 ? [node] : node.childNodes.flatMap(texts);
        const nodes = texts(scope); let index = 0;
        return { nextNode: () => nodes[index++] || null };
      },
    },
  };
  const previous = Object.fromEntries(Object.keys(globals).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  const restore = () => {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  };
  let mount;
  t.after(() => { try { mount?.unmount(); } finally { restore(); } });
  const capabilities = {
    project_id: 'p1', demo: { active: demo }, host_tools: hostTools,
    capabilities: Object.fromEntries(['authoring', 'exchange', 'assistance', 'handoff'].map((name) => [name, {
      requested: true, effective: !gated, reasons: gated ? [`${name} requires G0 and G1.`] : [], development_verification: development,
    }])),
  };
  if (demoAssistance) capabilities.capabilities.assistance = { requested: false, effective: false, reasons: [DEMO_ASSISTANCE_REASON] };
  const change = { change_id: 'c1', locator: 'team-leave-request', revision: 'rev1', state: archived ? 'Archived' : 'Draft', workspace_state: archived ? 'RELEASED' : 'ATTACHED', documents: [{ document_id: 'proposal', path: 'proposal.md', editable: true }], storyboard_frames: [] };
  const root = el('div'); document.body.append(root);
  if (draft !== null) localStorage.setItem('symphony.pm.draft.p1.c1.proposal.rev1', draft);
  mount = mountArtifacts({ root, projectId: 'p1', onDirtyChange: (dirty) => dirtyChanges.push(dirty), ui: { el, renderMarkdown: (source) => el('p', null, source), announce: (...args) => announcements.push(args) }, api: {
    request: async (path, options) => {
      const extra = await apiRequest?.(path, options); if (extra !== undefined) return extra;
      const pathname = path.split('?')[0];
      if (pathname === '/capabilities') return capabilities;
      if (pathname === '/changes') return { changes: [change] };
      if (pathname === '/changes/c1') return change;
      if (pathname.includes('/documents/')) return { document_id: pathname.split('/').at(-1), source: 'Demo document', path: 'proposal.md' };
      if (pathname.endsWith('/wbs')) return { tasks: [], findings: [] };
      if (pathname.endsWith('/managed-tickets')) return { tickets: [] };
      if (pathname.endsWith('/coverage')) return { entities: [] };
      throw new Error(`Unexpected PM request: ${path}`);
    },
  } });
  await new Promise((resolve) => setImmediate(resolve));
  if (lifecycle) lifecycle.unmount = () => mount.unmount();
  assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
  assert.ok(root.querySelector('.pm-revision-bar'), `PM renderer must finish without an error: ${root.textContent}`);
  return root;
}

function findButton(root, label) {
  const node = root.querySelectorAll('button').find((item) => item.textContent === label);
  assert.ok(node, `Missing button: ${label}`); return node;
}

test('document review/source toggles retain language, focus, draft and input handlers', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const dirtyChanges = [];
    const root = await mountedPm(context, { language, dirtyChanges });
    const layout = root.querySelector('.pm-document-layout');
    const toggle = layout.querySelector('.pm-view-switcher').querySelector('button');
    const source = layout.querySelector('textarea');
    const sourcePane = layout.querySelector('.pm-editor-pane');
    const reviewPane = layout.querySelector('.pm-preview-pane');
    assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Review document', {}, language));
    assert.equal(toggle.getAttribute('aria-pressed'), 'true');
    assert.equal(sourcePane.hidden, false);
    assert.equal(reviewPane.hidden, true);
    assert.equal(document.activeElement, source);
    source.value += '\nLocal draft';
    await source.trigger('input');
    assert.equal(dirtyChanges.at(-1), true);
    assert.equal(findButton(layout, pmText('Save draft', {}, language)).disabled, false);
    const draft = source.value;
    assert.equal(layout.querySelector('.pm-document-preview').textContent, draft);
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    assert.equal(toggle.getAttribute('aria-pressed'), 'false');
    assert.equal(sourcePane.hidden, true);
    assert.equal(reviewPane.hidden, false);
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Review document', {}, language));
    assert.equal(layout.querySelector('textarea'), source);
    assert.equal(source.value, draft);
    assert.equal(document.activeElement, source);
  });
});

test('Design direct source replacements localize both callbacks and the nested toggle', async (t) => {
  for (const language of ['en', 'ko']) for (const view of ['storyboard', 'ux', 'api']) {
    await t.test(`${view}, ${language}`, async (context) => {
      const root = await mountedPm(context, { tab: 'design', view, language });
      const surface = root.querySelector('.pm-design-stack');
      const selector = surface.querySelector('.pm-segmented');
      await findButton(surface, pmText('Edit source', {}, language)).trigger('click');
      assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
      assert.equal(surface.querySelector('.pm-segmented'), selector);
      findButton(surface, pmText(view === 'api' ? 'Add contract' : 'Add storyboard', {}, language));
      const layout = surface.querySelector('.pm-document-layout');
      assert.ok(layout, 'The document editor is rendered after the direct replacement');
      findButton(layout, pmText('Save draft', {}, language));
      const toggle = findButton(layout, pmText('Edit source', {}, language));
      await toggle.trigger('click');
      assert.equal(toggle.textContent, pmText('Review document', {}, language));
      assert.equal(document.activeElement, layout.querySelector('textarea'));
      assert.equal(layout.querySelector('textarea').value, 'Demo document');
      await toggle.trigger('click');
      assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    });
  }
});

test('R9 each area retains guide entries and starts closed in English and Korean', async (t) => {
  for (const language of ['en', 'ko']) for (const demo of [false, true]) for (const tab of ['idea', 'requirements', 'design', 'plan', 'code']) {
    await t.test(`${tab}, ${language}, demo=${demo}`, async (context) => {
      const root = await mountedPm(context, { tab, language, demo });
      const guides = root.querySelectorAll('details.pm-guide');
      assert.ok(guides.length >= 2, `${tab}: workspace and area guides must both be visible`);
      const entries = await guideEntries();
      for (const [area, labels] of [['workspace', GUIDE_LABELS.workspace], [tab, GUIDE_LABELS[tab]]]) {
        const guide = guides.find((node) => labels.every((label) => node.textContent.includes(pmText(label, {}, language))));
        assert.ok(guide, `${area}: missing visible guide labels`);
        assert.equal(guide.open, false, `${area}: help starts closed in demo and ordinary mode`);
        assert.equal(guide.querySelector('summary')?.textContent, pmText(GUIDE_SUMMARY, {}, language));
        for (const label of labels) {
          const text = guide.textContent;
          const translatedLabel = pmText(label, {}, language), sentence = pmText(entries[area][label], {}, language);
          assert.ok(text.indexOf(sentence, text.indexOf(translatedLabel) + translatedLabel.length) >= 0, `${area}/${label}: sentence must follow its label`);
        }
      }
    });
  }
});

test('round10 workspace guide joins the header controls after actions with unchanged order, help and status', async (t) => {
  for (const language of ['en', 'ko']) for (const demo of [false, true]) for (const tab of ['idea', 'requirements', 'design', 'plan', 'code']) {
    await t.test(`${tab}, ${language}, demo=${demo}`, async (context) => {
      const root = await mountedPm(context, { tab, language, demo });
      const header = root.querySelector('header.pm-header');
      const controls = header.querySelector('div.pm-header-controls');
      assert.equal(header.children.length, 2, 'The header keeps two columns and no separate guide row');
      assert.equal(header.children[0].className, 'pm-heading-copy');
      assert.equal(controls.parentNode, header.children[1], 'Round14 intentionally wraps the existing controls in its native disclosure');
      assert.equal(controls.parentNode.tag, 'details');
      assert.deepEqual(controls.children.map((node) => `${node.tag}.${node.className}`), ['div.pm-actions', 'details.pm-guide'], 'The workspace guide follows the actions');
      const guides = root.querySelectorAll('details.pm-guide');
      assert.equal(guides.length, 2);
      assert.equal(guides[0], controls.children[1], 'Workspace help stays first among the native disclosures');
      // DOM order is the Tab order; no tabindex is introduced.
      const focusables = controls.querySelectorAll('select, button, summary');
      assert.deepEqual(focusables.map((node) => node.tag === 'select' ? 'select' : node.textContent),
        ['select', ...['Repair workspace', 'Convert legacy plan', 'New change', GUIDE_SUMMARY].map((label) => pmText(label, {}, language))]);
      assert.ok(focusables.every((node) => node.getAttribute('tabindex') === null));
      const guide = guides[0];
      assert.equal(guide.open, false);
      assert.equal(guide.getAttribute('open'), null);
      const entries = await guideEntries();
      assert.deepEqual(guide.querySelectorAll('dt').map((node) => node.textContent), Object.keys(entries.workspace).map((label) => pmText(label, {}, language)));
      assert.deepEqual(guide.querySelectorAll('dd').map((node) => node.textContent), Object.values(entries.workspace).map((text) => pmText(text, {}, language)));
      const status = root.querySelector('section[id="pm-capability-status"]');
      if (!demo) { assert.equal(status, null); return; }
      assert.equal(status.getAttribute('role'), 'status');
      assert.equal(status.querySelector('strong').textContent, pmText(DEMO_HEADING, {}, language));
      assert.deepEqual(status.querySelector('ul.pm-capability-list').children.map((node) => node.textContent), [pmText(DEMO_SENTENCE, {}, language)]);
    });
  }
});

const settlePm = async () => { for (let i = 0; i < 3; i += 1) await new Promise((resolve) => setImmediate(resolve)); };

test('round7 selected option projects authoritative detail after approval/edit without changing list rows', async (t) => {
  const listed = [{ change_id: 'c1', locator: 'first', state: 'Validated' }, { change_id: 'c2', locator: 'second', state: 'Draft' }];
  const original = structuredClone(listed), calls = [];
  let detail = { ...listed[0], revision: 'r1', documents: [{ document_id: 'proposal', editable: true }], storyboard_frames: [] };
  const root = await mountedPm(t, { apiRequest: async (path, options) => {
    path = path.split('?')[0];
    calls.push(path);
    if (path === '/changes') return { changes: listed };
    if (path === '/changes/c1') return structuredClone(detail);
    if (path.endsWith('/approvals')) { detail = { ...detail, state: 'Approved' }; return {}; }
    if (path.includes('/documents/proposal') && options?.method === 'PATCH') { detail = { ...detail, state: 'Validated', revision: 'r2' }; return {}; }
  } });
  window.confirm = () => true;
  const options = () => root.querySelector('.pm-change-select').options.slice(1).map((node) => [node.value, node.textContent, Boolean(node.selected)]);
  await findButton(root, 'Approve revision').trigger('click'); await settlePm();
  assert.deepEqual(options(), [['c1', 'first · Approved', true], ['c2', 'second · Draft', false]]);
  await findButton(root, 'Edit source').trigger('click');
  const editor = root.querySelector('textarea'); editor.value += '\nEdited'; await editor.trigger('input');
  await findButton(root, 'Save draft').trigger('click'); await settlePm();
  assert.deepEqual(options(), [['c1', 'first · Validated', true], ['c2', 'second · Draft', false]]);
  assert.deepEqual(listed, original, 'cached list and unrelated row retain their objects and values');
  assert.equal(calls.filter((path) => path === '/changes').length, 1, 'refresh adds no list API call');
});

test('round7 header preserves failed-switch rollback and ignores stale aborted detail', async (t) => {
  const rows = [{ change_id: 'c1', locator: 'first', state: 'Draft' }, { change_id: 'c2', locator: 'second', state: 'Draft' }];
  const detail = (row, state = row.state) => ({ ...row, state, revision: 'r1', documents: [{ document_id: 'proposal', editable: true }] });
  let mode = 'fail', finish, pendingSignal;
  const root = await mountedPm(t, { apiRequest: async (path, options) => {
    path = path.split('?')[0];
    if (path === '/changes') return { changes: structuredClone(rows) };
    if (path === '/changes/c1') return detail(rows[0], 'Validated');
    if (path === '/changes/c2') {
      if (mode === 'fail') throw new Error('switch unavailable');
      pendingSignal = options.signal;
      return new Promise((resolve) => { finish = resolve; });
    }
  } });
  window.history = history;
  const choose = async (id) => { const select = root.querySelector('.pm-change-select'); select.value = id; await select.trigger('change'); await settlePm(); };
  await choose('c2');
  assert.equal(root.querySelector('.pm-change-select').value, 'c1');
  assert.match(root.querySelector('.pm-action-error').textContent, /switch unavailable/);
  assert.equal(root.querySelector('.pm-change-select').selectedOptions[0].textContent, 'first · Validated');
  mode = 'pending'; await choose('c2'); await choose('c1');
  assert.equal(pendingSignal.aborted, true);
  finish(detail(rows[1], 'Approved')); await settlePm();
  assert.equal(root.querySelector('.pm-change-select').value, 'c1');
  assert.deepEqual(root.querySelector('.pm-change-select').options.slice(1).map((node) => node.textContent), ['first · Validated', 'second · Draft']);
});

test('round7 archived detail missing from a multi-change list prepends exactly once', async (t) => {
  const rows = [{ change_id: 'c1', locator: 'first', state: 'Draft' }, { change_id: 'c2', locator: 'second', state: 'Draft' }];
  const root = await mountedPm(t, { requested: 'c3', apiRequest: async (path) => {
    path = path.split('?')[0];
    if (path === '/changes') return { changes: structuredClone(rows) };
    if (path === '/changes/c3') return { change_id: 'c3', locator: 'archive', state: 'Archived', workspace_state: 'RELEASED', revision: 'r3', documents: [{ document_id: 'proposal', editable: false }] };
  } });
  const assertRows = () => assert.deepEqual(root.querySelector('.pm-change-select').options.slice(1).map((node) => node.value), ['c3', 'c1', 'c2']);
  assertRows(); await findButton(root, 'Plan').trigger('click'); await settlePm(); assertRows();
  assert.equal(root.querySelector('.pm-change-select').value, 'c3');
});

test('round7 native Add child follows restored leaf/group selection and read-only precedence', async (t) => {
  for (const language of ['en', 'ko']) for (const gated of [false, true]) await t.test(`${language} gated=${gated}`, async (context) => {
    const tasks = [{ task_id: 'G-1', parent_id: null, order: 10, kind: 'Group', title: 'Group', executable: false }, { task_id: 'T-1', parent_id: 'G-1', order: 10, kind: 'Feature', title: 'Leaf', executable: true }];
    let refusedParent;
    const root = await mountedPm(context, { tab: 'plan', language, gated, apiRequest: async (path, options) => {
      if (!path.split('?')[0].endsWith('/wbs')) return undefined;
      if (options?.method === 'PATCH') { refusedParent = options.body.payload.operations[0].task.parent_id; throw new Error('missing_parent'); }
      return { tasks, findings: [] };
    } });
    const label = language === 'ko' ? '하위 추가' : 'Add child';
    const inspect = (leaf) => {
      const child = findButton(root, label), hint = root.querySelector('[id="pm-add-child-hint"]');
      assert.equal(child.disabled, gated || leaf);
      assert.ok(hint, 'a visible reason accompanies the disabled leaf action');
      assert.equal(hint.hidden, !leaf);
      assert.equal(child.getAttribute('aria-describedby'), leaf ? 'pm-add-child-hint' : null);
      assert.equal(child.title, gated ? 'authoring requires G0 and G1.' : leaf ? hint.textContent : '');
      if (leaf) assert.equal(hint.textContent, language === 'ko' ? '하위 작업을 추가하려면 그룹을 선택하세요.' : 'Select a group to add a child task.');
    };
    assert.equal(findButton(root, label).disabled, gated, 'genuine no-selection keeps root creation available');
    await root.querySelector('[data-task-id="T-1"]').trigger('click'); inspect(true);
    await root.querySelector('[data-task-id="G-1"]').trigger('click'); inspect(false);
    sessionStorage.setItem('symphony.pm.p1.c1.selectedTask', 'T-missing');
    await findButton(root, language === 'ko' ? '아이디어' : 'Idea').trigger('click'); await settlePm();
    await findButton(root, language === 'ko' ? '계획' : 'Plan').trigger('click'); await settlePm();
    // Missing persisted IDs remain nonempty; the server still decides missing_parent.
    // restoreViewState runs on a change reload, so select the current change again.
    const select = root.querySelector('.pm-change-select'); select.value = 'c1'; await select.trigger('change'); await settlePm();
    inspect(false);
    if (!gated) {
      await findButton(root, label).trigger('click'); await settlePm();
      assert.equal(refusedParent, 'T-missing');
      assert.match(root.querySelector('.pm-action-error').textContent, /missing_parent/);
    }
    await root.querySelector('[data-task-id="T-1"]').trigger('click'); inspect(true);
    await findButton(root, language === 'ko' ? '아이디어' : 'Idea').trigger('click'); await settlePm();
    await findButton(root, language === 'ko' ? '계획' : 'Plan').trigger('click'); await settlePm(); inspect(true);
    await root.querySelector('[data-task-id="G-1"]').trigger('click'); inspect(false);
  });
});

test('round12 Indent pressed before the Outdent refresh lands runs on the refreshed WBS', async (t) => {
  const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
  const group = { task_id: 'G-1', parent_id: null, order: -1, kind: 'Group', title: 'Group', executable: false };
  const child = { task_id: 'T-1', parent_id: 'G-1', order: 0, kind: 'Feature', title: 'Leaf', executable: true };
  let server = { revision: 'rev1', tasks: [group, child] };
  const patches = [], outdentPatch = deferred(), refreshChange = deferred();
  let holdRefresh = false;
  const root = await mountedPm(t, { tab: 'plan', apiRequest: async (path, options) => {
    const pathname = path.split('?')[0];
    if (pathname === '/changes/c1') {
      if (holdRefresh) { holdRefresh = false; await refreshChange.promise; }
      return { change_id: 'c1', locator: 'team-leave-request', revision: server.revision, state: 'Draft', workspace_state: 'ATTACHED', documents: [], storyboard_frames: [] };
    }
    if (!pathname.endsWith('/wbs')) return undefined;
    if (options?.method !== 'PATCH') return { tasks: server.tasks, findings: [] };
    patches.push(options.body);
    const [operation] = options.body.payload.operations;
    if (patches.length === 1) { await outdentPatch.promise; holdRefresh = true; }
    server = { revision: `rev${patches.length + 1}`, tasks: server.tasks.map((task) => (task.task_id === operation.task_id ? { ...task, [operation.field]: operation.value } : task)) };
    return { impacted_ids: [operation.task_id] };
  } });
  await root.querySelector('[data-task-id="T-1"]').trigger('click');
  const outdent = findButton(root, 'Outdent').trigger('click'); await settlePm();
  assert.deepEqual(patches.map((body) => [body.expected_revision, body.payload.operations]), [['rev1', [{ op: 'set_field', task_id: 'T-1', field: 'parent_id', value: null }]]]);
  // PATCH 200 returns; the change refresh is still pending, so the outgoing view still shows T-1 as G-1's first child.
  outdentPatch.resolve(); await settlePm();
  assert.equal(root.querySelector('[data-task-id="T-1"]').getAttribute('aria-level'), '2');
  const staleIndent = findButton(root, 'Indent'); staleIndent.focus();
  const indent = staleIndent.trigger('click'); await settlePm();
  refreshChange.resolve(); await Promise.all([outdent, indent]); await settlePm();
  assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
  assert.deepEqual(patches.map((body) => [body.expected_revision, body.payload.operations]), [
    ['rev1', [{ op: 'set_field', task_id: 'T-1', field: 'parent_id', value: null }]],
    ['rev2', [{ op: 'set_field', task_id: 'T-1', field: 'parent_id', value: 'G-1' }]],
  ]);
  assert.notEqual(patches[0].client_operation_id, patches[1].client_operation_id);
  assert.equal(root.querySelector('.pm-revision-bar').textContent.includes('rev3'), true, root.querySelector('.pm-revision-bar').textContent);
  assert.equal(root.querySelector('[data-task-id="T-1"]').getAttribute('aria-level'), '2');
  assert.equal(document.activeElement, root.querySelector('[aria-current="page"]'), 'mutation refresh keeps the existing current-tab focus');
  // T-1 is again G-1's first child, so a further Indent is genuinely ineligible and stays a no-op.
  await findButton(root, 'Indent').trigger('click'); await settlePm();
  assert.equal(patches.length, 2);
});

// Two changes with their own WBS; every PATCH waits until the test releases it.
async function heldWbsPm(t, { failures = [], announcements = [] } = {}) {
  const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
  const rows = [{ change_id: 'c1', locator: 'team-leave-request', state: 'Draft' }, { change_id: 'c2', locator: 'other-change', state: 'Draft' }];
  const servers = {
    c1: { version: 1, tasks: [{ task_id: 'G-1', parent_id: null, order: -1, kind: 'Group', title: 'Group', executable: false }, { task_id: 'T-1', parent_id: 'G-1', order: 0, kind: 'Feature', title: 'Leaf', executable: true }] },
    c2: { version: 1, tasks: [{ task_id: 'G-9', parent_id: null, order: 10, kind: 'Group', title: 'Other group', executable: false }] },
  };
  const patches = [], gates = [];
  const gate = (index) => (gates[index] ||= deferred());
  const root = await mountedPm(t, { tab: 'plan', announcements, apiRequest: async (path, options) => {
    const pathname = path.split('?')[0];
    if (pathname === '/changes') return { changes: structuredClone(rows) };
    const [, , id, resource] = pathname.split('/'), server = servers[id];
    if (!server) return undefined;
    if (!resource) return { ...rows.find((row) => row.change_id === id), revision: `${id}-rev${server.version}`, workspace_state: 'ATTACHED', documents: [], storyboard_frames: [] };
    if (resource !== 'wbs') return undefined;
    if (options?.method !== 'PATCH') return { tasks: server.tasks, findings: [] };
    const index = patches.length, [operation] = options.body.payload.operations;
    patches.push([id, options.body.expected_revision, operation.op]);
    await gate(index).promise;
    if (failures.includes(index)) throw new Error('Outdent was rejected.');
    server.version += 1;
    if (operation.op === 'create_task') {
      const task_id = `${id}-NEW-${index}`; server.tasks = [...server.tasks, { ...operation.task, task_id }];
      return { impacted_ids: [task_id] };
    }
    server.tasks = server.tasks.map((task) => (task.task_id === operation.task_id ? { ...task, [operation.field]: operation.value } : task));
    return { impacted_ids: [operation.task_id] };
  } });
  const choose = async (id) => { const select = root.querySelector('.pm-change-select'); select.value = id; await select.trigger('change'); await settlePm(); };
  // Outdent T-1 and hold its PATCH, then press Add group so it waits in the command queue.
  const queueBehindOutdent = async () => {
    await root.querySelector('[data-task-id="T-1"]').trigger('click');
    const outdent = findButton(root, 'Outdent').trigger('click'); await settlePm();
    const addGroup = findButton(root, 'Add group').trigger('click'); await settlePm();
    assert.deepEqual(patches, [['c1', 'c1-rev1', 'set_field']], 'Add group waits behind the pending Outdent');
    return { settled: Promise.all([outdent, addGroup]) };
  };
  return { root, patches, release: (index) => gate(index).resolve(), choose, queueBehindOutdent };
}

test('round13 a queued WBS command is dropped when its change is left before it runs', async (t) => {
  for (const route of [['c2'], ['c2', 'c1']]) await t.test(`c1→${route.join('→')}`, async (context) => {
    const { root, patches, release, choose, queueBehindOutdent } = await heldWbsPm(context);
    const commands = await queueBehindOutdent();
    for (const id of route) await choose(id);
    const current = route.at(-1);
    assert.equal(root.querySelector('.pm-change-select').value, current);
    release(0); await settlePm();
    assert.deepEqual(patches, [['c1', 'c1-rev1', 'set_field']], 'the old view\'s queued Add group writes to no change');
    await commands.settled;
    // A command pressed in the current view still runs, once, against that change.
    const fresh = findButton(root, 'Add group').trigger('click'); await settlePm();
    assert.deepEqual(patches[1], [current, current === 'c1' ? 'c1-rev2' : 'c2-rev1', 'create_task']);
    release(1); await fresh; await settlePm();
    assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
    assert.equal(patches.length, 2);
  });
});

test('round13 a queued WBS command is dropped when PM unmounts before it runs', async (t) => {
  let held;
  await t.test('mounted', async (context) => {
    held = await heldWbsPm(context);
    held.commands = await held.queueBehindOutdent();
  });
  // The subtest's teardown has unmounted PM; releasing the held Outdent must not issue the queued write.
  held.release(0); await settlePm();
  assert.deepEqual(held.patches, [['c1', 'c1-rev1', 'set_field']]);
  await held.commands.settled;
});

test('round13 a rejected first WBS command is reported and the queued command runs once without replay', async (t) => {
  const announcements = [];
  const { root, patches, release, queueBehindOutdent } = await heldWbsPm(t, { failures: [0], announcements });
  const commands = await queueBehindOutdent();
  release(0); await settlePm();
  assert.match(root.querySelector('.pm-action-error').textContent, /Outdent was rejected\./);
  assert.ok(announcements.some(([message, kind]) => kind === 'error' && /Outdent was rejected\./.test(message)));
  assert.deepEqual(patches, [['c1', 'c1-rev1', 'set_field'], ['c1', 'c1-rev1', 'create_task']], 'the failed Outdent is not replayed');
  release(1); await commands.settled; await settlePm();
  assert.equal(patches.length, 2);
  assert.ok(root.querySelector('[data-task-id="c1-NEW-1"]'), root.textContent);
  assert.equal(root.querySelector('.pm-revision-bar').textContent.includes('c1-rev2'), true);
  // T-1 stayed G-1's first child, so Indent remains a genuine no-op.
  await root.querySelector('[data-task-id="T-1"]').trigger('click');
  assert.equal(root.querySelector('[data-task-id="T-1"]').getAttribute('aria-level'), '2');
  await findButton(root, 'Indent').trigger('click'); await settlePm();
  assert.equal(patches.length, 2);
});

test('round13 a queued WBS command is dropped when its Plan tab or WBS subview is left before it runs', async (t) => {
  for (const route of [['Idea'], ['Idea', 'Plan'], ['Dependencies', 'Work breakdown'], ['Plan document', 'Work breakdown']]) await t.test(`WBS→${route.join('→')}`, async (context) => {
    const { root, patches, release, queueBehindOutdent } = await heldWbsPm(context);
    const commands = await queueBehindOutdent();
    for (const label of route) { await findButton(root, label).trigger('click'); await settlePm(); }
    release(0); await settlePm();
    assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
    assert.deepEqual(patches, [['c1', 'c1-rev1', 'set_field']], 'the left view\'s queued Add group is not sent');
    await commands.settled;
    if (route.at(-1) === 'Idea') { await findButton(root, 'Plan').trigger('click'); await settlePm(); }
    // A command pressed in the current WBS view still runs, once, on the refreshed revision.
    const fresh = findButton(root, 'Add group').trigger('click'); await settlePm();
    assert.deepEqual(patches[1], ['c1', 'c1-rev2', 'create_task']);
    release(1); await fresh; await settlePm();
    assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
    assert.equal(patches.length, 2);
  });
});

async function loadI18n({ href, stored = null, storageDisabled = false }) {
  const source = await readFile(new URL('../src/symphony/web/static/i18n.js', import.meta.url), 'utf8');
  const values = new Map(stored === null ? [] : [['symphony.lang', stored]]), writes = [], replacements = [];
  const location = new URL(href);
  const history = { state: { retained: true }, replaceState: (state, title, url) => { replacements.push(String(url)); location.href = new URL(url, location).href; } };
  const document = { documentElement: { lang: '' }, querySelectorAll: () => [] };
  const window = { location, history };
  const localStorage = {
    getItem: (key) => { if (storageDisabled) throw new Error('Storage disabled'); return values.get(key) ?? null; },
    setItem: (key, value) => { if (storageDisabled) throw new Error('Storage disabled'); writes.push([key, String(value)]); values.set(key, String(value)); },
  };
  vm.runInNewContext(source, { window, document, location, history, localStorage, URL, URLSearchParams }, { filename: 'i18n.js' });
  return { i18n: window.i18n, document, values, writes, replacements, location };
}

test('R10 ?lang=ko overrides storage, persists like the switch, and is consumed before reload', async () => {
  for (const stored of [null, 'en', 'ko']) {
    const page = await loadI18n({ href: 'http://127.0.0.1/?keep=1&lang=ko&pmTab=design#/artifacts', stored });
    assert.equal(page.i18n.lang, 'ko');
    assert.equal(page.document.documentElement.lang, 'ko');
    assert.equal(page.values.get('symphony.lang'), 'ko');
    assert.equal(page.location.searchParams.has('lang'), false);
    assert.equal(page.location.searchParams.get('keep'), '1');
    assert.equal(page.location.searchParams.get('pmTab'), 'design');
    assert.equal(page.location.hash, '#/artifacts');
    assert.ok(page.replacements.length > 0, 'Consume lang through history.replaceState');
    let changed;
    page.i18n.onChange((language) => { changed = language; });
    page.i18n.setLang('en');
    assert.equal(changed, 'en');
    assert.deepEqual(page.writes.at(-1), ['symphony.lang', 'en']);
    const reload = await loadI18n({ href: page.location.href, stored: page.values.get('symphony.lang') });
    assert.equal(reload.i18n.lang, 'en', 'Reload must preserve a later manual language choice (P3)');
  }
});

test('R10 query values are exactly en or ko; xx and ko-KR keep stored or default language', async () => {
  for (const value of ['xx', 'ko-KR', 'KO']) for (const stored of [null, 'en', 'ko']) {
    const page = await loadI18n({ href: `http://127.0.0.1/?lang=${value}#/artifacts`, stored });
    assert.equal(page.i18n.lang, stored || 'en', `${value}, stored=${stored}`);
    assert.equal(page.document.documentElement.lang, stored || 'en');
    assert.deepEqual(page.writes, [], 'An invalid URL value must not change storage');
  }
  const english = await loadI18n({ href: 'http://127.0.0.1/?lang=en#/artifacts', stored: 'ko' });
  assert.equal(english.i18n.lang, 'en');
  assert.equal(english.values.get('symphony.lang'), 'en');
  assert.equal(english.location.searchParams.has('lang'), false);
});

test('R10 URL language still applies and is consumed when localStorage is unavailable', async () => {
  const page = await loadI18n({ href: 'http://127.0.0.1/?lang=ko&keep=1#/artifacts', storageDisabled: true });
  assert.equal(page.i18n.lang, 'ko');
  assert.equal(page.document.documentElement.lang, 'ko');
  assert.equal(page.location.searchParams.has('lang'), false);
  assert.equal(page.location.searchParams.get('keep'), '1');
});

test('R15 Node titles follow P6, gate approval/archive, and leave Validate enabled', async (t) => {
  for (const gated of [false, true]) for (const node of [undefined, { available: true, reason: null }, { available: false, reason: NODE_REASON }]) {
    await t.test(`gated=${gated}, node=${node?.available ?? 'absent'}`, async (context) => {
      const root = await mountedPm(context, { tab: 'plan', gated, hostTools: node ? { node } : {} });
      const controls = [['Approve revision', 'authoring'], ['Preview archive', 'handoff'], ['Validate', null]];
      const expected = controls.map(([label, capability]) => {
        if (!capability) return { label, disabled: false, title: node?.available === false ? NODE_REASON : '' };
        const oldTitle = gated ? `${capability} requires G0 and G1.` : '';
        const title = node?.available === false ? [oldTitle, NODE_REASON].filter(Boolean).join(' ') : oldTitle;
        return { label, disabled: gated || node?.available === false, title };
      });
      const actual = controls.map(([label]) => { const button = findButton(root, label); return { label, disabled: button.disabled, title: button.title }; });
      assert.deepEqual({ controls: actual, toolReasonShown: Boolean(root.querySelector('.pm-status-card')?.textContent.includes(NODE_REASON)) },
        { controls: expected, toolReasonShown: node?.available === false });
    });
  }
  await t.test('archived controls keep only the read-only title', async (context) => {
    const root = await mountedPm(context, { tab: 'plan', archived: true, gated: true, hostTools: { node: { available: false, reason: NODE_REASON } } });
    for (const label of ['Approve revision', 'Preview archive', 'Validate']) {
      assert.equal(findButton(root, label).title, ARCHIVED_REASON, label);
      assert.equal(findButton(root, label).disabled, true, label);
    }
  });
});

test('R15 unavailable Codex folds into assistance and lists capability/login reasons up front', async (t) => {
  for (const gated of [false, true]) for (const codex of [undefined, { available: true, reason: null }, { available: false, reason: CODEX_REASON }]) {
    await t.test(`gated=${gated}, codex=${codex?.available ?? 'absent'}`, async (context) => {
      const root = await mountedPm(context, { gated, hostTools: codex ? { codex } : {} });
      const button = findButton(root, 'Planning assistant');
      assert.equal(button.disabled, gated || codex?.available === false);
      if (codex?.available === false) {
        assert.ok(button.title.includes(CODEX_REASON), button.title);
        assert.ok(root.querySelector('.pm-status-card')?.textContent.includes(CODEX_REASON), 'Status card lists the unavailable Codex reason');
        if (gated) assert.ok(button.title.includes('assistance requires G0 and G1.'));
      } else assert.equal(button.title, gated ? 'assistance requires G0 and G1.' : '');
    });
  }
});

test('R15 demo status and assistance reason render in en/ko in place of development text', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const root = await mountedPm(context, { demo: true, language, development: true, demoAssistance: true });
    const card = root.querySelector('.pm-status-card');
    assert.ok(card);
    assert.equal(card.querySelector('strong').textContent, pmText(DEMO_HEADING, {}, language));
    assert.ok(card.textContent.includes(pmText(DEMO_SENTENCE, {}, language)));
    assert.ok(card.textContent.includes(pmText(DEMO_ASSISTANCE_REASON, {}, language)));
    assert.ok(!card.textContent.includes(DEVELOPMENT_SENTENCE));
    assert.ok(!card.textContent.includes('Development verification context'));
    assertKorean([DEMO_HEADING, DEMO_SENTENCE, DEMO_ASSISTANCE_REASON]);
    const button = findButton(root, pmText('Planning assistant', {}, language));
    assert.equal(button.disabled, true);
    assert.ok(button.title.includes(DEMO_ASSISTANCE_REASON) || button.title.includes(pmText(DEMO_ASSISTANCE_REASON, {}, language)));
  });
  await t.test('demo status is visible even without unavailable actions or development flags', async (context) => {
    const root = await mountedPm(context, { demo: true });
    const card = root.querySelector('.pm-status-card');
    assert.ok(card, 'An active demo always identifies itself');
    assert.equal(card.querySelector('strong').textContent, DEMO_HEADING);
    assert.ok(card.textContent.includes(DEMO_SENTENCE));
  });
  await t.test('ordinary development status is preserved', async (context) => {
    const root = await mountedPm(context, { development: true });
    const card = root.querySelector('.pm-status-card');
    assert.equal(card.querySelector('strong').textContent, 'Development verification context');
    assert.ok(card.textContent.includes(DEVELOPMENT_SENTENCE));
    assert.ok(!card.textContent.includes(DEMO_SENTENCE));
  });
});


test('hardening recovered source is dirty against saved document and immediately savable', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const dirtyChanges = [];
    const root = await mountedPm(context, { language, draft: 'Recovered unsaved text', dirtyChanges });
    assert.equal(root.querySelector('textarea').value, 'Recovered unsaved text');
    assert.equal(findButton(root, pmText('Save draft', {}, language)).disabled, false);
    assert.equal(dirtyChanges.at(-1), true);
    root.querySelector('textarea').value = 'Demo document';
    await root.querySelector('textarea').trigger('input');
    assert.equal(findButton(root, pmText('Save draft', {}, language)).disabled, true);
    assert.equal(dirtyChanges.at(-1), false);
  });
});

test('hardening empty assistant review omits absent nodes without null text or authority changes', async (t) => {
  const calls = [];
  const root = await mountedPm(t, { apiRequest: (path, options) => {
    if (path.endsWith('/planning-actions')) { calls.push(options); return { operation_id: 'op-empty', result: { explanation: 'No changes suggested.', proposals: [], blocked_instruction: null, reserved_ids: {} } }; }
  } });
  await findButton(root, 'Planning assistant').trigger('click');
  await findButton(document.body, 'Prepare proposal').trigger('click');
  await new Promise((resolve) => setImmediate(resolve));
  const dialog = document.body.querySelector('[aria-label="Planning proposal"]');
  assert.ok(dialog, document.body.textContent);
  assert.ok(!dialog.textContent.includes('null'), dialog.textContent);
  assert.ok(!dialog.textContent.includes('undefined'), dialog.textContent);
  assert.equal(findButton(dialog, 'Apply proposal').disabled, true);
  assert.equal(calls.length, 1);
});

test('hardening Korean tab discard is localized and cancellation retains unsaved input', async (t) => {
  const root = await mountedPm(t, { language: 'ko' });
  const editor = root.querySelector('textarea'); editor.value = 'Unsaved'; await editor.trigger('input');
  let message; window.confirm = (value) => { message = value; return false; };
  await findButton(root, '설계').trigger('click');
  assert.match(message, /저장하지 않은/);
  assert.equal(root.querySelector('textarea').value, 'Unsaved');
});


test('hardening owned overlay refuses dirty replacement and restores focus and original inert values', async (t) => {
  const root = await mountedPm(t);
  const trigger = findButton(root, 'Edit source'); trigger.focus();
  const unrelated = new UiNode('section'); unrelated.inert = true; document.body.append(unrelated);
  const overlay = createPmOverlay(root);
  const panel = new UiNode('section'), close = new UiNode('button'), last = new UiNode('input'); panel.append(close, last);
  let mayClose = false;
  assert.equal(overlay.open(panel, { canClose: () => mayClose }), true);
  assert.equal(root.inert, true); assert.equal(document.activeElement, close);
  await panel.trigger('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(document.activeElement, last);
  assert.equal(overlay.open(new UiNode('section')), false);
  assert.equal(overlay.panel, panel);
  assert.equal(overlay.close(), false);
  mayClose = true;
  assert.equal(overlay.close(), true);
  assert.equal(root.inert, undefined); assert.equal(unrelated.inert, true);
  assert.equal(document.activeElement, trigger);
  assert.equal(document.body.querySelector('.pm-overlay-backdrop'), null);
  overlay.open(new UiNode('section'));
  overlay.dispose();
  assert.equal(document.body.querySelector('.pm-overlay-backdrop'), null);
  assert.equal(root.inert, undefined);
  overlay.destroy();
  assert.equal(overlay.open(new UiNode('section')), false, 'Late responses cannot reopen an unmounted project overlay');
});

test('hardening delayed previews cannot open or confirm on a later PM view', async (t) => {
  for (const kind of ['handoffs', 'archive']) await t.test(kind, async (context) => {
    let complete;
    const waiting = new Promise((resolve) => { complete = resolve; });
    const root = await mountedPm(context, { tab: 'plan', apiRequest: (path) => path.endsWith(`/${kind}/preview`) ? waiting : undefined });
    const pending = findButton(root, kind === 'handoffs' ? 'Preview approved handoff' : 'Preview archive').trigger('click');
    await new Promise((resolve) => setImmediate(resolve));
    await findButton(root, 'Code Files').trigger('click');
    complete(kind === 'handoffs' ? { items: [], preview_token: 'old-view-token' } : { candidate: { target_paths: ['old-view-archive'] }, accepted_commits: {}, archive_id: 'old-view-archive', confirmation_token: 'old-token' });
    await pending;
    assert.equal(document.body.querySelector('[role="dialog"]'), null, 'An old preview must not create a dialog on the new view');
    assert.equal(root.querySelector('.pm-action-error'), null);
  });
});

test('hardening detached confirmation cannot submit after PM navigation', async (t) => {
  let writes = 0;
  const root = await mountedPm(t, { tab: 'plan', apiRequest: (path) => {
    if (path.endsWith('/handoffs/preview')) return { items: [], preview_token: 'view-token' };
    if (path.endsWith('/handoffs')) { writes += 1; return {}; }
  } });
  await findButton(root, 'Preview approved handoff').trigger('click');
  const confirm = findButton(document.body, 'Create native tickets');
  await findButton(root, 'Code Files').trigger('click');
  await confirm.trigger('click');
  assert.equal(writes, 0, 'An event queued on the detached old confirmation cannot authorize a new view');
  assert.equal(document.body.querySelector('[role="dialog"]'), null);
});

test('hardening failed source save keeps dirty input and allows an immediate retry', async (t) => {
  const dirtyChanges = [];
  const root = await mountedPm(t, { dirtyChanges, apiRequest: (path, options) => {
    if (options?.method === 'PATCH' && path.includes('/documents/')) throw new Error('Fixture save failed; retry.');
  } });
  const source = root.querySelector('textarea'); source.value += '\nRetry marker'; await source.trigger('input');
  const save = findButton(root, 'Save draft'); await save.trigger('click');
  assert.equal(source.value, 'Demo document\nRetry marker');
  assert.ok(root.querySelector('.pm-action-error').textContent.includes('Fixture save failed'));
  assert.equal(save.disabled, false, 'Failure must not masquerade as a saved clean document');
  assert.equal(dirtyChanges.at(-1), true);
});


test('round5 exact archive guidance preserves unknown failures and silent aborts', async (t) => {
  for (const language of ['en', 'ko']) for (const variant of ['exact', 'other-message', 'other-code', 'abort']) {
    await t.test(`${language}/${variant}`, async (context) => {
      const announcements = [];
      const error = Object.assign(new Error(variant === 'other-message' ? 'Another approval is required' : 'archive requires the approved revision'), {
        code: variant === 'other-code' ? 'different_code' : 'approval_required', name: variant === 'abort' ? 'AbortError' : 'Error',
      });
      const root = await mountedPm(context, { tab: 'plan', language, announcements, apiRequest: (path) => {
        if (path.includes('/archive/preview')) throw error;
      } });
      const source = findButton(root, pmText('Preview archive', {}, language));
      await source.trigger('click');
      const alert = root.querySelector('.pm-action-error');
      if (variant === 'abort') { assert.equal(alert, null); assert.deepEqual(announcements, []); return; }
      assert.equal(alert.getAttribute('role'), 'alert');
      assert.equal(document.activeElement, source);
      const expected = variant === 'exact' ? (language === 'ko'
        ? '승인된 리비전이 있어야 아카이브할 수 있습니다. 현재 리비전을 검토하고 승인한 뒤 아카이브 미리보기를 다시 여세요.'
        : 'Archive requires an approved revision. Review and approve this revision, then preview archive again.') : error.message;
      assert.ok(alert.textContent.includes(`${error.code}: ${expected}`), alert.textContent);
      assert.deepEqual(announcements, [[`${error.code}: ${expected}`, 'error']]);
      assert.equal(error.message, variant === 'other-message' ? 'Another approval is required' : 'archive requires the approved revision');
    });
  }
});


test('round5 validation guidance changes only the exact Problem finding', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const findings = [
      { code: 'idea_field_required', field: 'problem', message: 'Idea field problem is required', severity: 'error' },
      { code: 'idea_field_required', field: 'goal', message: 'Idea field goal is required', severity: 'error' },
      { code: 'other_code', field: 'problem', message: 'Idea field problem is required', severity: 'error' },
      { code: 'idea_field_required', field: 'problem', message: 'Different problem message', severity: 'error' },
    ];
    const snapshot = JSON.stringify(findings);
    const root = await mountedPm(context, { language, apiRequest: (path) => path.endsWith('/validate') ? { valid: false, findings } : undefined });
    await findButton(root, pmText('Validate', {}, language)).trigger('click');
    const dialog = document.body.querySelector('[role="dialog"]');
    assert.ok(dialog);
    const rows = dialog.querySelectorAll('.pm-finding');
    assert.equal(rows.length, 4);
    assert.ok(rows[0].textContent.includes(language === 'ko'
      ? '문제 항목이 빠져 있습니다. 아이디어 소스에 문제 항목을 추가하고 저장한 뒤 다시 검증하세요.'
      : 'The Problem field is missing. Add it to the idea source, save, then validate again.'), rows[0].textContent);
    for (let index = 1; index < rows.length; index++) assert.ok(rows[index].textContent.includes(findings[index].message));
    for (let index = 0; index < rows.length; index++) assert.equal(rows[index].querySelector('strong').textContent, findings[index].code);
    assert.equal(JSON.stringify(findings), snapshot, 'The server findings, fields and codes remain untouched');
  });
});


test('round5 all six compact-table consumers retain rows, column headings and local wrappers', async (t) => {
  const task = { task_id: 'T-1', parent_id: null, order: 1, kind: 'Feature', title: 'A task', dependencies: [{ task_id: 'T-0', kind: 'hard', justification: 'Required first' }], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [] };
  const apiRequest = (path) => {
    path = path.split('?')[0];
    if (path.endsWith('/wbs')) return { tasks: [task], findings: [] };
    if (path.endsWith('/wbs/preview')) return { findings: [] };
    if (path.endsWith('/coverage')) return { entities: [{ semantic_id: 'REQ-12345678', title: 'Coverage', task_links: [{ task_id: 'T-1', task_revision: 'revision-1', ticket_identifier: 'TICKET-1', implemented: true, tested: false, accepted: false, test_provenance: 'manual_reported' }] }] };
    if (path.endsWith('/managed-tickets')) return { tickets: [{ ticket_identifier: 'TICKET-1', task_id: 'T-1', task_revision: 'revision-1', code_files: [{ status: 'M', path: 'src/a-very-long-path.js' }] }] };
    if (path.endsWith('/planning-actions')) return { operation_id: 'op-1', result: { reserved_ids: { '$new': 'REQ-87654321' } } };
  };
  const inspect = (scope, expectedHeaders, expectedText) => {
    const table = scope.querySelectorAll('.pm-compact-table').find((node) => node.querySelectorAll('th').map((th) => th.textContent).join('|') === expectedHeaders.join('|'));
    assert.ok(table, `Missing ${expectedHeaders.join('/')}`);
    assert.equal(table.parentNode.className, 'pm-table-scroll');
    assert.ok(table.closest('.pm-workspace-host') || table.closest('.pm-overlay-backdrop'));
    assert.ok(table.querySelectorAll('th').every((th) => th.getAttribute('scope') === 'col'));
    assert.ok(table.textContent.includes(expectedText), table.textContent);
    return table;
  };
  for (const language of ['en', 'ko']) for (const consumer of ['dependencies', 'coverage', 'code', 'planning', 'paste', 'proposal']) {
    await t.test(`${language}/${consumer}`, async (context) => {
      const tab = consumer === 'proposal' ? 'idea' : ['code', 'planning', 'coverage'].includes(consumer) ? 'code' : 'plan';
      const root = await mountedPm(context, { tab, view: consumer === 'dependencies' ? 'dependencies' : '', language, apiRequest });
      const headers = (values) => values.map((value) => pmText(value, {}, language));
      // Round9 F2: dependency headers come from the scoped catalog; English is unchanged.
      if (consumer === 'dependencies') inspect(root, language === 'en' ? headers(['Task', 'Needs', 'Dependency', 'Reason']) : ['작업', '선행 작업', '의존 유형', '사유'], 'Required first');
      if (consumer === 'coverage') inspect(root, headers(['Task', 'Revision', 'Ticket', 'Implemented', 'Tested', 'Accepted', 'Provenance']), 'TICKET-1');
      if (consumer === 'code') inspect(root, headers(['Status', 'Path']), 'src/a-very-long-path.js');
      if (consumer === 'planning') inspect(root, language === 'ko' ? ['경로', '상태', '역할'] : headers(['Path', 'State', 'Role']), 'proposal.md');
      if (consumer === 'paste') {
        await root.querySelector('.pm-wbs-table').trigger('paste', { clipboardData: { getData: () => 'T-1\ttitle\tChanged title' } });
        const dialog = document.body.querySelector('[role="dialog"]');
        inspect(dialog, headers(['Task', 'Field', 'Value']), 'Changed title');
        await findButton(dialog, pmText('Cancel', {}, language)).trigger('click');
        assert.equal(document.body.querySelector('[role="dialog"]'), null);
      }
      if (consumer === 'proposal') {
        await findButton(root, pmText('Planning assistant', {}, language)).trigger('click');
        const dialog = document.body.querySelector('[role="dialog"]');
        await findButton(dialog, pmText('Prepare proposal', {}, language)).trigger('click');
        const proposal = document.body.querySelector('[role="dialog"]');
        inspect(proposal, headers(['Placeholder', 'Permanent ID']), 'REQ-87654321');
        assert.ok(proposal.closest('.pm-overlay-backdrop'));
      }
    });
  }
});


test('round5 shared toast adapter keeps ordinary host semantics and sets PM roles before append', async () => {
  const source = await readFile(new URL('../src/symphony/web/static/app.js', import.meta.url), 'utf8');
  const start = source.indexOf('  function showToast(');
  const toastSource = source.slice(start, source.indexOf('\n  // ------------------------------------------------------------------', start));
  const appended = [], pending = [];
  const el = (tag, attrs, text) => {
    const node = new UiNode(tag); node.textContent = text;
    for (const [key, value] of Object.entries(attrs)) if (value !== null && value !== undefined) node.setAttribute(key, value);
    return node;
  };
  const sandbox = { el, document: { getElementById: () => ({ appendChild: (node) => appended.push({node, role: node.getAttribute('role')}) }) }, setTimeout: (fn) => { pending.push(fn); return pending.length; }, clearTimeout() {} };
  vm.runInNewContext(`${toastSource}; globalThis.toast = showToast;`, sandbox);
  for (const type of ['info', 'success', 'error']) {
    sandbox.toast(`Host ${type}`, type);
    assert.equal(appended.at(-1).role, type === 'error' ? 'alert' : 'status');
    assert.equal(appended.at(-1).node.className, `toast toast-${type}`);
  }
  const adapter = source.match(/announce:\s*(\([^)]*\)\s*=>\s*(?:\{[\s\S]*?\n {10}\}|showToast\([^\n]+\)))/);
  assert.ok(adapter, 'Find the actual PM host adapter');
  sandbox.showToast = sandbox.toast;
  const announce = vm.runInNewContext(`(${adapter[1]})`, sandbox);
  announce('PM saved');
  assert.ok(appended.at(-1).node.className.includes('pm-toast'));
  assert.equal(appended.at(-1).role, 'status');
  announce('PM failed', 'error');
  const error = appended.at(-1);
  assert.ok(error.node.className.includes('toast-error'));
  assert.ok(error.node.className.includes('pm-toast'));
  assert.equal(error.role, null, 'PM inline error is already the live alert when the companion toast is appended');
  assert.equal(error.node.getAttribute('aria-live'), null);
  assert.equal(pending.length, 5);
});


test('round6 modal table focus cycle includes only current overflow and excludes non-tab stops', async (t) => {
  const owner = await mountedPm(t);
  const make = (tag, attrs = {}, text = '') => {
    const node = new UiNode(tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.append(text);
    return node;
  };
  const tableRegion = (scrollWidth, clientWidth) => {
    const wrapper = make('div', { class: 'pm-table-scroll' });
    const table = make('table', { class: 'pm-compact-table' });
    const row = make('tr');
    row.append(...['Task', 'Field', 'Value'].map((name) => make('th', { scope: 'col' }, name)));
    table.append(row); wrapper.append(table);
    wrapper.scrollWidth = scrollWidth; wrapper.clientWidth = clientWidth;
    return wrapper;
  };
  const overflow = tableRegion(460, 160), fitting = tableRegion(120, 200);
  const first = make('button', {}, 'Apply'), last = make('button', {}, 'Cancel');
  const negativeBefore = make('div', { tabindex: '-2' }), negativeAfter = make('div', { tabindex: '-1' });
  const disabled = make('button', { disabled: '' }, 'Disabled');
  const hiddenParent = make('section', { hidden: '' }); hiddenParent.append(make('input'));
  const dialog = make('section', { role: 'dialog' });
  dialog.append(negativeBefore, overflow, fitting, first, last, negativeAfter, disabled, hiddenParent);
  const opener = findButton(owner, 'Edit source'); opener.focus();
  const overlay = createPmOverlay(owner); t.after(() => overlay.destroy());
  assert.equal(overlay.open(dialog, { initialFocus: first }), true);
  last.focus(); await dialog.trigger('keydown', { key: 'Tab', shiftKey: false });
  assert.equal(document.activeElement, overflow, 'Forward modal wrap reaches the scroll region before Apply');
  assert.equal(overflow.getAttribute('role'), 'region');
  assert.equal(overflow.getAttribute('aria-label'), 'Task, Field, Value');
  assert.equal(overflow.tabIndex, 0);
  assert.equal(fitting.tabIndex, -1, 'A fitting table adds no tab stop');
  await dialog.trigger('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(document.activeElement, last);
  // Re-evaluate actual overflow when the dialog width changes while it stays open.
  fitting.scrollWidth = 500; overflow.scrollWidth = 100;
  last.focus(); await dialog.trigger('keydown', { key: 'Tab', shiftKey: false });
  assert.equal(document.activeElement, fitting);
  assert.equal(overflow.tabIndex, -1);
  fitting.scrollWidth = 100;
  last.focus(); await dialog.trigger('keydown', { key: 'Tab', shiftKey: false });
  assert.equal(document.activeElement, first, 'Negative, disabled and hidden candidates are not tabbable');
  await dialog.trigger('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(document.activeElement, last);
  await dialog.trigger('keydown', { key: 'Escape' });
  assert.equal(document.activeElement, opener);
  assert.equal(document.body.querySelector('[role="dialog"]'), null);
});


// Round9 F1: PM-only readable projection of managed metadata.
const TASKS_FIXTURE = [
  '# Implementation plan',
  '<!-- symphony-wbs-format: 1 -->',
  '',
  'Use one managed group block per group and one managed task checkbox per executable leaf.',
  '',
  '<!-- symphony-task:T-E67463FA -->',
  '- [ ] Prepare bounded proposal',
  '```yaml symphony-task',
  'id: T-E67463FA', 'parent_id: null', 'order: 1', 'type: Feature', 'priority: null', 'human_owner: null', 'agent_kind: null',
  'depends_on: []', 'requirements: []', 'storyboards: []', 'design_refs: []', 'target_date: null', 'estimate: null',
  '```',
  '',
  '### Scope',
  'Prepare a bounded proposal.',
  '<!-- /symphony-task -->',
  '',
].join('\n');
const TASKS_READABLE = '# Implementation plan\n\nUse one managed group block per group and one managed task checkbox per executable leaf.\n\n- [ ] Prepare bounded proposal\n\n### Scope\nPrepare a bounded proposal.\n';
const NESTED_FIXTURE = [
  '<!-- symphony-group:G-ONE -->', '## Group one', '```yaml symphony-group', 'id: G-ONE', 'parent_id: null', 'order: 1', '```',
  '<!-- symphony-task:T-ONE -->', '- [ ] First task', '```yaml symphony-task', 'id: T-ONE', 'parent_id: G-ONE', '```', '<!-- /symphony-task -->',
  '<!-- /symphony-group -->',
].join('\n');
// Every guard contains managed-looking text that is not a complete top-level managed span.
const READABLE_GUARDS = {
  markdownFence: '# Documentation example\n\n```markdown\n<!-- symphony:req=REQ-1 -->\n#### Scenario: Example\n<!-- symphony:scenario=SC-1 -->\n```\n',
  quote: '# Documentation example\n\n> <!-- symphony:req=REQ-1 -->\n> #### Scenario: Example\n> <!-- symphony:scenario=SC-1 -->\n',
  longerFence: '# Documentation example\n\n````markdown\n```\n<!-- symphony:req=REQ-1 -->\n#### Scenario: Example\n<!-- symphony:scenario=SC-1 -->\n````\n',
  nestedManagedFence: '````markdown\n<!-- symphony-task:T-X -->\n- [ ] Example\n```yaml symphony-task\nid: T-X\n```\n<!-- /symphony-task -->\n````\nAfter example.',
  indented: 'Example:\n\n    <!-- symphony:req=REQ-1 -->\n    <!-- symphony-wbs-format: 1 -->\n\tid: T-X',
  comments: '<!-- user note -->\n<!-- symphony-note: keep -->\n<!-- symphony-task:bad id -->\nBody',
  ordinaryFences: '```mermaid\nflowchart LR\nA-->B\n```\n```ts\nconst value = 1;\n```',
  unclosed: '<!-- symphony-task:T-A -->\n- [ ] A\n```yaml symphony-task\nid: T-A\n\nFollowing prose.',
  unmatchedClose: 'Intro\n<!-- /symphony-task -->\nOutro',
  plain: '# Plain\n\nNo managed metadata here.\n',
};

function documentApi(sources, { change = null, baselines = {}, wbs = null, patches = [] } = {}) {
  return (path, options) => {
    const pathname = path.split('?')[0];
    if (options?.method === 'PATCH' && pathname.includes('/documents/')) { patches.push(options.body); return {}; }
    if (change && pathname === '/changes') return { changes: [change] };
    if (change && pathname === '/changes/c1') return change;
    const doc = pathname.match(/\/documents\/([^/]+)$/);
    const id = doc ? decodeURIComponent(doc[1]) : '';
    if (doc && sources[id] !== undefined) return { document_id: id, source: sources[id], path: `${id}.md` };
    const baseline = pathname.match(/\/baselines\/([^/]+)$/);
    if (baseline) return baselines[decodeURIComponent(baseline[1])] ?? { exists: false };
    if (wbs && pathname.endsWith('/wbs')) return wbs;
    return undefined;
  };
}

test('round9 F1 Plan document readable view hides complete managed metadata and keeps source/save bytes', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const patches = [];
    const root = await mountedPm(context, { tab: 'plan', view: 'source', language, apiRequest: documentApi({ tasks: TASKS_FIXTURE }, { patches }) });
    const layout = root.querySelector('.pm-document-layout');
    const preview = layout.querySelector('.pm-document-preview');
    const source = layout.querySelector('textarea');
    assert.equal(preview.textContent, TASKS_READABLE, 'initial readable renderer input');
    assert.equal(source.value, TASKS_FIXTURE, 'textarea keeps the exact source');
    source.value = `${TASKS_FIXTURE}<!-- symphony:decision=DEC-ABC12345 -->\nAdded note\n`;
    await source.trigger('input');
    assert.equal(preview.textContent, `${TASKS_READABLE}Added note\n`, 'input refresh uses the same projection');
    await findButton(layout, pmText('Save draft', {}, language)).trigger('click');
    assert.equal(patches.length, 1);
    assert.equal(patches[0].payload.operations[0].source, `${TASKS_FIXTURE}<!-- symphony:decision=DEC-ABC12345 -->\nAdded note\n`, 'PATCH body keeps raw bytes');
  });
});

test('round9 F1 readable heading keeps its accessible name while visually compact', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const root = await mountedPm(context, { language });
    const heading = root.querySelector('.pm-preview-readable').querySelector('h2');
    assert.equal(heading.textContent, pmText('Readable view', {}, language));
    assert.equal(heading.getAttribute('class'), 'sr-only');
    const css = await readFile(new URL('../packages/pm/src/symphony/pm/static/pm.css', import.meta.url), 'utf8');
    assert.match(css, /\.pm-preview-readable \{[^}]*padding: 24px clamp\(24px, 5vw, 64px\) clamp\(24px, 5vw, 64px\);/);
    assert.match(css, /@media \(max-width: 900px\)[\s\S]*\.pm-preview-readable \{ padding: 24px 18px; \}/, 'mobile override unchanged');
  });
});

test('round9 F1 nested group/task display fixture hides four markers and two YAML blocks', async (t) => {
  const root = await mountedPm(t);
  const source = root.querySelector('textarea');
  source.value = NESTED_FIXTURE; await source.trigger('input');
  assert.equal(root.querySelector('.pm-document-preview').textContent, '## Group one\n- [ ] First task');
  source.value = `<!-- symphony:req=REQ-ABC12345 -->\nReal requirement\n${READABLE_GUARDS.markdownFence}`; await source.trigger('input');
  assert.equal(root.querySelector('.pm-document-preview').textContent, `Real requirement\n${READABLE_GUARDS.markdownFence}`, 'top-level marker removed, fenced example kept');
});

test('round9 F1 preservation guards keep examples, unknown, malformed and unclosed text unchanged', async (t) => {
  const root = await mountedPm(t);
  const source = root.querySelector('textarea');
  for (const [name, value] of Object.entries(READABLE_GUARDS)) {
    source.value = value; await source.trigger('input');
    assert.equal(root.querySelector('.pm-document-preview').textContent, value, name);
  }
  assert.equal(root.querySelector('textarea').value, READABLE_GUARDS.plain);
});

test('round9 F1 readableSource is identity without recognized spans and keeps CRLF on retained lines', () => {
  const { readableSource } = pmDocumentsModule;
  assert.equal(typeof readableSource, 'function');
  for (const [name, value] of Object.entries(READABLE_GUARDS)) assert.equal(readableSource(value), value, name);
  assert.equal(readableSource('A\r\n<!-- symphony:req=REQ-ABC12345 -->\r\nB\r\n'), 'A\r\nB\r\n');
  assert.equal(readableSource('   <!-- symphony-wbs-format: 1 -->  \nkept'), 'kept');
  assert.equal(readableSource('<!-- symphony-task:T-A -->\nA\n<!-- /symphony-group -->'), '<!-- symphony-task:T-A -->\nA\n<!-- /symphony-group -->', 'kind mismatch stays visible');
  assert.equal(readableSource('<!-- symphony-group:G-A -->\n```yaml symphony-task\nid: T-A\n```\n<!-- /symphony-group -->'), '```yaml symphony-task\nid: T-A\n```', 'YAML kind must match its block');
  assert.equal(readableSource(''), '');
});

test('round9 F1 Requirements projects both proposed and baseline while delta/source stay raw', async (t) => {
  const proposed = '# Requirements\n\n### Requirement: New\n<!-- symphony:req=REQ-NEW22222 -->\nNew text\n```markdown\n<!-- symphony:req=REQ-1 -->\n```\n';
  const baseline = '# Requirements\n\n### Requirement: Old\n<!-- symphony:req=REQ-OLD11111 -->\nOld text\n';
  const change = { change_id: 'c1', locator: 'team-leave-request', revision: 'rev1', state: 'Draft', workspace_state: 'ATTACHED', storyboard_frames: [], documents: [{ document_id: 'proposal', path: 'proposal.md', editable: true }, { document_id: 'spec:requirements', path: 'specs/requirements/spec.md', editable: true }] };
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const root = await mountedPm(context, { tab: 'requirements', language, apiRequest: documentApi({ 'spec:requirements': proposed }, { change, baselines: { requirements: { exists: true, source: baseline } } }) });
    const [left, right] = root.querySelector('.pm-requirement-delta').children;
    assert.equal(left.querySelector('p').textContent, '# Requirements\n\n### Requirement: New\nNew text\n```markdown\n<!-- symphony:req=REQ-1 -->\n```\n');
    assert.equal(right.querySelector('p').textContent, '# Requirements\n\n### Requirement: Old\nOld text\n');
    assert.equal(root.querySelector('.pm-count').textContent, '+6 / −3 lines from baseline', 'line delta uses raw sources');
    assert.equal(root.querySelector('.pm-source-details').querySelector('pre').textContent, proposed);
  });
});

test('round9 F1 Design projects the full document before section extraction', async (t) => {
  const real = '# Design\n\n## Architecture\n<!-- symphony:contract=API-ABC12345 -->\nReal architecture text\n## Integration\n<!-- symphony:contract=API-DEF67890 -->\nIntegration text\n';
  const example = '# Design\n````markdown\n## Architecture\n<!-- symphony-task:T-X -->\n```yaml symphony-task\nid: T-X\n```\n<!-- /symphony-task -->\n````\n## Architecture\nreal';
  const cases = [
    ['architecture', real, '## Architecture\nReal architecture text'],
    ['erd', real, '# Design\n\n## Architecture\nReal architecture text\n## Integration\nIntegration text\n'],
    // Preservation guard: the first matching heading sits inside a user fence, so the raw extract is kept.
    ['architecture', example, '## Architecture\n<!-- symphony-task:T-X -->\n```yaml symphony-task\nid: T-X\n```\n<!-- /symphony-task -->\n````'],
  ];
  for (const [view, source, expected] of cases) await t.test(`${view}/${source === real ? 'real' : 'example'}`, async (context) => {
    const patches = [];
    const root = await mountedPm(context, { tab: 'design', view, apiRequest: documentApi({ design: source }, { patches }) });
    const surface = root.querySelector('.pm-design-stack');
    assert.equal(surface.querySelector('.pm-document-preview').textContent, expected);
    await findButton(surface, 'Edit source').trigger('click');
    const editor = surface.querySelector('textarea');
    assert.equal(editor.value, source, 'Design source editor keeps full raw bytes');
    editor.value = `${source}\nEdited`; await editor.trigger('input');
    await findButton(surface, 'Save draft').trigger('click');
    assert.equal(patches[0].payload.operations[0].source, `${source}\nEdited`);
  });
});

// S4 metadata ends only at an exact triple-backtick line; a longer closer leaves the YAML unrecognized.
const LONGER_CLOSER = '<!-- symphony-task:T-A -->\n- [ ] A\n```yaml symphony-task\nid: T-A\nFollowing prose.\n````\n<!-- /symphony-task -->';
const LONGER_CLOSER_READABLE = '- [ ] A\n```yaml symphony-task\nid: T-A\nFollowing prose.\n````';

test('round9 F1 readableSource keeps managed-looking YAML whose closer is not exact triple backticks', () => {
  const { readableSource } = pmDocumentsModule;
  assert.equal(readableSource(LONGER_CLOSER), LONGER_CLOSER_READABLE, 'four-backtick closer keeps YAML and prose');
  assert.equal(readableSource(`${NESTED_FIXTURE.replace('order: 1\n```', 'order: 1\n```  ')}`), '## Group one\n- [ ] First task', 'exact triple closer with trailing spaces is still metadata');
  assert.equal(readableSource('```ts\nconst value = 1;\n````\n<!-- symphony:req=REQ-ABC12345 -->\nAfter'), '```ts\nconst value = 1;\n````\nAfter', 'ordinary fences still close on a longer closer');
});

test('round9 F1 editor and Design readable views keep YAML with a four-backtick closer', async (t) => {
  for (const language of ['en', 'ko']) {
    await t.test(`editor/${language}`, async (context) => {
      const root = await mountedPm(context, { tab: 'plan', view: 'source', language, apiRequest: documentApi({ tasks: LONGER_CLOSER }) });
      assert.equal(root.querySelector('.pm-document-preview').textContent, LONGER_CLOSER_READABLE);
      assert.equal(root.querySelector('textarea').value, LONGER_CLOSER, 'textarea keeps the exact source');
    });
    await t.test(`design/${language}`, async (context) => {
      const design = `# Design\n\n## Architecture\n${LONGER_CLOSER}\n`;
      const root = await mountedPm(context, { tab: 'design', view: 'architecture', language, apiRequest: documentApi({ design }) });
      assert.equal(root.querySelector('.pm-design-stack').querySelector('.pm-document-preview').textContent, `## Architecture\n${LONGER_CLOSER_READABLE}\n`);
    });
  }
});

// Round9 F2: UI-owned dependency/placeholder text, without new walker keys.
const DEPENDENCY_WBS = { findings: [], tasks: [
  { task_id: 'T-A', parent_id: null, order: 1, kind: 'Feature', title: 'Order only', dependencies: [], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [] },
  { task_id: 'T-B', parent_id: null, order: 2, kind: 'Feature', title: 'Task', dependencies: [{ task_id: 'T-A', kind: 'requires_code', justification: 'Reason' }], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [] },
  { task_id: 'T-C', parent_id: null, order: 3, kind: 'Feature', title: 'Requires code', dependencies: [{ task_id: 'T-B', kind: 'order_only', justification: 'Requires code' }, { task_id: 'T-GONE', kind: 'mystery_kind', justification: 'Order only' }], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [] },
] };
const DEPENDENCY_TEXT = {
  en: { headers: ['Task', 'Needs', 'Dependency', 'Reason'], requires: 'Requires code', order: 'Order only', option: (task, need) => `${task} needs ${need}` },
  ko: { headers: ['작업', '선행 작업', '의존 유형', '사유'], requires: '코드 완료 필요', order: '순서만 지정', option: (task, need) => `${task} — 선행 작업: ${need}` },
};
const USER_STRINGS = ['Task', 'Needs', 'Reason', 'Requires code', 'Order only'];

function assertDependencyTable(root, language) {
  const text = DEPENDENCY_TEXT[language];
  const table = root.querySelector('.pm-compact-table');
  assert.deepEqual(table.querySelectorAll('th').map((th) => th.textContent), text.headers);
  const rows = table.querySelector('tbody').querySelectorAll('tr').map((tr) => tr.querySelectorAll('td'));
  assert.deepEqual(rows.map((cells) => cells.map((cell) => cell.textContent)), [
    ['T-B · Task', 'T-A · Order only', text.requires, 'Reason'],
    ['T-C · Requires code', 'T-B · Task', text.order, 'Requires code'],
    ['T-C · Requires code', 'T-GONE', 'mystery_kind', 'Order only'],
  ]);
  assert.deepEqual(rows.map((cells) => [cells[2].getAttribute('data-kind'), cells[2].getAttribute('title')]), [
    ['requires_code', 'requires_code'], ['order_only', 'order_only'], ['mystery_kind', 'mystery_kind'],
  ]);
}

test('round9 F2 dependency table names both tasks in direction with localized known kinds', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const snapshot = JSON.stringify(DEPENDENCY_WBS);
    const root = await mountedPm(context, { tab: 'plan', view: 'dependencies', language, apiRequest: documentApi({}, { wbs: DEPENDENCY_WBS }) });
    assertDependencyTable(root, language);
    // A later render runs localizePm over the whole tree again; user values and labels stay exact.
    await findButton(root, pmText('Work breakdown', {}, language)).trigger('click');
    await findButton(root, pmText('Dependencies', {}, language)).trigger('click');
    assertDependencyTable(root, language);
    assert.equal(JSON.stringify(DEPENDENCY_WBS), snapshot, 'payload unchanged');
  });
});

test('round9 F2 planning dialog placeholders and dependency options stay localized after rebuilds', async (t) => {
  const change = { change_id: 'c1', locator: 'team-leave-request', revision: 'rev1', state: 'Draft', workspace_state: 'ATTACHED', storyboard_frames: [],
    documents: [{ document_id: 'proposal', path: 'proposal.md', editable: true }, { document_id: 'tasks', path: 'tasks.md', editable: true }],
    planning_sections: [{ document_id: 'proposal', section_id: 'proposal:reason', label: 'Reason' }, { document_id: 'tasks', section_id: 'tasks:task', label: 'Task' }] };
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const root = await mountedPm(context, { language, apiRequest: documentApi({ proposal: 'Reason' }, { change, wbs: DEPENDENCY_WBS }) });
    assert.equal(root.querySelector('.pm-document-preview').textContent, 'Reason', 'user preview paragraph is not translated');
    await findButton(root, pmText('Planning assistant', {}, language)).trigger('click');
    const dialog = document.body.querySelector('[role="dialog"]');
    const [action, artifact, section, dependency] = dialog.querySelectorAll('select');
    const placeholder = pmText('Choose section', {}, language);
    assert.deepEqual(section.options.map((item) => item.textContent), [placeholder, 'Reason']);
    artifact.value = 'tasks'; await artifact.trigger('change');
    assert.deepEqual(section.options.map((item) => item.textContent), [placeholder, 'Task'], 'document switch rebuild');
    artifact.value = 'proposal'; await artifact.trigger('change');
    action.value = 'refine_section'; await action.trigger('change');
    assert.deepEqual(section.options.map((item) => [item.value, item.textContent]), [['', placeholder], ['proposal:reason', 'Reason']], 'action switch rebuild');
    const text = DEPENDENCY_TEXT[language];
    assert.deepEqual(dependency.options.map((item) => [item.value, item.textContent]), [
      ['', pmText('Choose dependency', {}, language)],
      ['T-B|T-A', text.option('T-B · Task', 'T-A · Order only')],
      ['T-C|T-B', text.option('T-C · Requires code', 'T-B · Task')],
      ['T-C|T-GONE', text.option('T-C · Requires code', 'T-GONE')],
    ]);
  });
});

test('round9 F2 scoped message catalog stays outside the walked KO dictionary', () => {
  const { pmMessage } = pmIndexModule;
  assert.equal(typeof pmMessage, 'function');
  for (const value of USER_STRINGS) assert.equal(pmText(value, {}, 'ko'), value, `${value} must not enter the walked KO map`);
  for (const key of ['pm.deps.col.task', 'pm.deps.col.needs', 'pm.deps.col.kind', 'pm.deps.col.reason', 'pm.deps.kind.requires_code', 'pm.deps.kind.order_only', 'pm.deps.option']) {
    for (const language of ['en', 'ko']) assert.notEqual(pmMessage(key, {}, language), key, `${key}/${language}`);
  }
  assert.equal(pmMessage('pm.deps.col.task', {}, 'en'), 'Task');
  assert.equal(pmMessage('pm.deps.col.task', {}, 'ko'), '작업');
  assert.equal(pmMessage('pm.deps.option', { task: 'B', predecessor: 'A' }, 'ko'), 'B — 선행 작업: A');
  assert.equal(pmMessage('pm.missing.key', {}, 'ko'), 'pm.missing.key');
  assert.equal(pmMessage('pm.deps.option', { task: 'T-B · {predecessor}', predecessor: 'T-A' }, 'en'), 'T-B · {predecessor} needs T-A', 'user titles are not re-interpolated');
  for (const value of Object.values(DEPENDENCY_TEXT.ko).flat().filter((item) => typeof item === 'string')) assert.equal(pmText(value, {}, 'ko'), value);
});

// Round14 models media notifications and DOM observations, not CSS layout or
// native Tab order. Actual visibility, geometry and keyboard behavior need the
// installed-browser receipt. No product exports or dependency are added.
function round14Media(matches) {
  const handlers = new Set();
  return {
    matches, media: '(max-width: 560px)', events: 0,
    addEventListener(type, handler) { if (type === 'change') handlers.add(handler); },
    removeEventListener(type, handler) { if (type === 'change') handlers.delete(handler); },
    get listenerCount() { return handlers.size; },
    async resize(compact) {
      this.matches = compact; this.events += 1;
      for (const handler of [...handlers]) await handler({ matches: compact, media: this.media });
    },
  };
}
function round14Disclosure(root, key) {
  const node = root.querySelector(`details.pm-more-${key}`);
  assert.ok(node, `Missing native ${key} disclosure`);
  assert.ok(node.querySelector('summary'), `${key} needs its native summary`);
  return node;
}
function round14ObserveRects(root, media) {
  for (const node of [root, ...root.querySelectorAll('*')]) {
    // Supplied observations isolate routing from CSS: all-width summaries have
    // rectangles, and native-closed descendants may retain rectangles as observed.
    // Actual painting, native Tab order and computed geometry remain browser proof.
    node.getClientRects = () => {
      if (!node.isConnected || node.hidden) return [];
      return [{ left: 0, top: 0, right: 44, bottom: 44, width: 44, height: 44 }];
    };
  }
}

test('round16 secondary controls start closed at every width while retaining warning and revision identity', async (t) => {
  for (const language of ['en', 'ko']) for (const compact of [true, false]) await t.test(`${language}, compact=${compact}`, async (context) => {
    const media = round14Media(compact);
    const root = await mountedPm(context, { tab: 'plan', language, demo: true, media });
    for (const key of ['change', 'status', 'revision', 'wbs']) assert.equal(round14Disclosure(root, key).open, false, `${key} starts closed`);
    assert.equal(media.events, 0, 'Initial compact layout must not depend on resize');
    const changes = round14Disclosure(root, 'change'), revision = round14Disclosure(root, 'revision'), status = root.querySelector('.pm-status-card');
    assert.ok(changes.contains(root.querySelector('.pm-change-select')));
    assert.ok(revision.contains(findButton(root, pmText('Approve revision', {}, language))));
    const statusOwner = round14Disclosure(root, 'status'), statusHeading = status.querySelector('strong');
    assert.notEqual(statusOwner, revision, 'Status and revision retain distinct native disclosure owners');
    for (let parent = statusHeading.parentElement; parent && root.contains(parent); parent = parent.parentElement) {
      if (parent.tag === 'details' && !parent.open) assert.ok(parent.children.find((child) => child.tag === 'summary')?.contains(statusHeading), 'Warning heading stays visible outside closed owners or in their native first summary');
    }
    assert.equal(status.querySelector('strong').textContent, pmText(DEMO_HEADING, {}, language));
    assert.equal(round14Disclosure(root, 'status').querySelector('summary').textContent, language === 'ko' ? '데모 작업공간 · 일회용 · AI 작업자 없음 · 로컬 전용' : 'Demo workspace · Disposable · no AI worker · stays local');
    assert.ok(status.textContent.includes(pmText(DEMO_SENTENCE, {}, language)), 'The full warning remains available');
    const identity = root.querySelector('.pm-revision-bar');
    const locator = identity.querySelector('strong'), rawRevision = identity.querySelector('code'), state = identity.querySelector('.pm-state');
    assert.equal(locator.textContent, 'team-leave-request');
    assert.equal(rawRevision.title, 'rev1');
    assert.equal(state.textContent, language === 'ko' ? '초안' : 'Draft');
    for (const node of [locator, rawRevision, state]) for (let parent = node.parentElement; parent && root.contains(parent); parent = parent.parentElement) {
      if (parent.tag === 'details' && !parent.open) assert.ok(parent.children.find((child) => child.tag === 'summary')?.contains(node), 'Selected context stays in visible content or the native first summary');
    }
    for (const key of ['change', 'status', 'revision', 'wbs']) {
      const disclosure = round14Disclosure(root, key); disclosure.open = true; await disclosure.trigger('toggle');
      assert.equal(disclosure.open, true, `${key} can be opened without replacing its controls`);
    }
    assert.ok(round14Disclosure(root, 'wbs').contains(root.querySelector('.pm-plan-filter')));
  });
});

test('round16 first Plan mount places original archive and handoff inside closed WBS actions at every width', async (t) => {
  for (const compact of [true, false]) await t.test(`compact=${compact}`, async (context) => {
    const media = round14Media(compact), root = await mountedPm(context, { tab: 'plan', media });
    const archive = findButton(root, 'Preview archive'), handoff = findButton(root, 'Preview approved handoff');
    const row = archive.parentElement, wbs = root.querySelector('.pm-plan-surface'), disclosure = round14Disclosure(root, 'wbs');
    assert.equal(handoff.parentElement, row);
    assert.equal(media.events, 0, 'Check the first committed render without resize');
    assert.equal(disclosure.open, false, 'Secondary WBS actions start closed at every width');
    assert.ok(wbs.contains(disclosure));
    assert.ok(disclosure.contains(archive) && disclosure.contains(handoff), 'Both original actions belong inside the WBS disclosure on first mount');
    assert.equal(row.previousElementSibling, disclosure.querySelector('summary'));
    disclosure.open = true; await disclosure.trigger('toggle');
    assert.ok(disclosure.contains(root.querySelector('.pm-plan-filter')), 'Opening exposes the original filter and actions');
    for (const label of ['Preview archive', 'Preview approved handoff']) assert.equal(root.querySelectorAll('button').filter((node) => node.textContent === label).length, 1);
  });
});

test('round16 all-width WBS placement retains open intent action identity handlers and capability guards', async (t) => {
  for (const gated of [false, true]) await t.test(`gated=${gated}`, async (context) => {
    const media = round14Media(false), calls = [];
    const root = await mountedPm(context, { tab: 'plan', media, gated, hostTools: { node: { available: false, reason: 'Node is unavailable.' } }, apiRequest: (path) => {
      if (path.includes('/handoffs/preview')) { calls.push(path); return { items: [], preview_token: 'p1' }; }
    } });
    const buttons = ['Preview archive', 'Preview approved handoff'].map((label) => findButton(root, label));
    const before = buttons.map((node) => ({ disabled: node.disabled, title: node.title, capability: node.dataset.capability, describedBy: node.getAttribute('aria-describedby'), handlers: [...node.listeners.get('click')] }));
    const opened = round14Disclosure(root, 'wbs'); opened.open = true; await opened.trigger('toggle');
    for (const compact of [true, false, true, false]) {
      await media.resize(compact);
      const disclosure = round14Disclosure(root, 'wbs');
      assert.equal(disclosure, opened);
      assert.equal(disclosure.open, true, 'Changing width preserves the explicit user-open intent');
      buttons.forEach((node, index) => {
        assert.equal(findButton(root, node.textContent), node, 'Move the original node');
        assert.equal(root.querySelectorAll('button').filter((item) => item.textContent === node.textContent).length, 1, 'No duplicate controls');
        assert.deepEqual({ disabled: node.disabled, title: node.title, capability: node.dataset.capability, describedBy: node.getAttribute('aria-describedby'), handlers: [...node.listeners.get('click')] }, before[index]);
        assert.equal(disclosure.contains(node), true);
      });
    }
    if (gated) {
      assert.equal(buttons[1].disabled, true); assert.equal(buttons[1].dataset.capability, 'handoff');
      assert.match(buttons[1].title, /handoff requires G0 and G1/);
      assert.equal(calls.length, 0);
    } else {
      await buttons[1].trigger('click'); await settlePm();
      assert.equal(calls.length, 1, 'One click still invokes one original preview handler');
      assert.ok(document.body.querySelector('[role="dialog"]'));
    }
    assert.equal(buttons[0].disabled, true, 'The missing-Node archive guard survives moves');
  });
});

test('round16 an opened revision retains user intent and focused control across widths', async (t) => {
  const media = round14Media(false), root = await mountedPm(t, { media });
  round14ObserveRects(root, media);
  const revision = round14Disclosure(root, 'revision'); revision.open = true; await revision.trigger('toggle');
  const approve = findButton(root, 'Approve revision'); approve.focus();
  const count = approve.focusCalls.length;
  for (const compact of [true, false]) {
    await media.resize(compact);
    assert.equal(revision.open, true, 'A width change must not close an explicitly opened revision');
    assert.equal(document.activeElement, approve);
    assert.equal(approve.focusCalls.length, count, 'Already visible owned focus is not moved');
  }
});

test('round16 visible change summary retains focus and closed state when widening', async (t) => {
  const media = round14Media(true), root = await mountedPm(t, { media });
  round14ObserveRects(root, media);
  const disclosure = round14Disclosure(root, 'change'), summary = disclosure.querySelector('summary'); summary.focus();
  const count = summary.focusCalls.length;
  await media.resize(false);
  assert.equal(disclosure.open, false);
  assert.equal(document.activeElement, summary);
  assert.equal(summary.focusCalls.length, count, 'The all-width visible summary keeps focus without a forced transfer');
});

test('round16 control-free status summary remains visible focused and closed across widths', async (t) => {
  const media = round14Media(true), root = await mountedPm(t, { tab: 'plan', demo: true, media });
  round14ObserveRects(root, media);
  const disclosure = round14Disclosure(root, 'status');
  assert.equal(disclosure.querySelectorAll('select, input, button, a[href]').length, 0);
  const summary = disclosure.querySelector('summary'); summary.focus();
  const count = summary.focusCalls.length;
  for (const compact of [false, true]) {
    await media.resize(compact);
    assert.equal(disclosure.open, false);
    assert.equal(document.activeElement, summary, 'The visible control-free summary does not require a fallback');
    assert.equal(summary.focusCalls.length, count);
  }
});

test('round14 breakpoint changes leave outside-root and already-visible focus alone', async (t) => {
  const media = round14Media(false), root = await mountedPm(t, { media }); round14ObserveRects(root, media);
  const outside = new UiNode('button'); document.body.append(outside); outside.focus();
  await media.resize(true); assert.equal(document.activeElement, outside);
  await media.resize(false); assert.equal(document.activeElement, outside);
  const current = root.querySelector('.pm-tabs').querySelector('[aria-current="page"]'); current.focus();
  const count = current.focusCalls.length;
  await media.resize(true); assert.equal(document.activeElement, current); assert.equal(current.focusCalls.length, count);
  await media.resize(false); assert.equal(document.activeElement, current); assert.equal(current.focusCalls.length, count);
});

test('round16 user-open disclosures and owned summary focus survive approval render at every width', async (t) => {
  for (const compact of [true, false]) await t.test(`compact=${compact}`, async (context) => {
    const media = round14Media(compact); let approvals = 0;
    const root = await mountedPm(context, { media, apiRequest: (path) => { if (path.split('?')[0].endsWith('/approvals')) { approvals += 1; return {}; } } });
    assert.equal(round14Disclosure(root, 'revision').open, false, 'Every width starts closed');
    window.confirm = () => true;
    const disclosure = round14Disclosure(root, 'revision'); disclosure.open = true; await disclosure.trigger('toggle');
    disclosure.querySelector('summary').focus();
    await findButton(root, 'Approve revision').trigger('click'); await settlePm();
    const rebuilt = round14Disclosure(root, 'revision');
    assert.equal(approvals, 1); assert.notEqual(rebuilt, disclosure, 'Exercise the actual fresh render');
    assert.equal(rebuilt.open, true, 'Keep the user-open state');
    assert.equal(document.activeElement, rebuilt.querySelector('summary'), 'Restore the owned summary after the normal current-tab focus');
    assert.deepEqual(document.activeElement.focusCalls.at(-1), { preventScroll: true });
  });
});

test('round14 Plan to non-Plan to Plan has no stale row and unmount releases media handler', async (t) => {
  const media = round14Media(true), lifecycle = {};
  const root = await mountedPm(t, { tab: 'plan', media, lifecycle });
  const original = findButton(root, 'Preview archive');
  await findButton(root, 'Idea').trigger('click'); await settlePm();
  assert.equal(root.contains(original), false);
  assert.equal(root.querySelectorAll('button').some((node) => node.textContent === 'Preview archive'), false);
  await media.resize(false); await media.resize(true);
  await findButton(root, 'Plan').trigger('click'); await settlePm();
  const replacement = findButton(root, 'Preview archive');
  assert.notEqual(replacement, original);
  assert.ok(round14Disclosure(root, 'wbs').contains(replacement), 'Place only the current Plan row');
  assert.equal(media.listenerCount, 1);
  const current = root.querySelector('.pm-tabs').querySelector('[aria-current="page"]'); current.focus();
  lifecycle.unmount(); assert.equal(media.listenerCount, 0);
  const open = round14Disclosure(root, 'wbs').open; await media.resize(false);
  assert.equal(round14Disclosure(root, 'wbs').open, open, 'Disposed mount must not respond to a later media event');
  assert.equal(document.activeElement, current);
});

test('round14 owned PlanningState labels translate six exact values and preserve unknown EN raw and user text', async (t) => {
  const states = [['Draft', '초안'], ['Validated', '검증됨'], ['NeedsReview', '검토 필요'], ['Approved', '승인됨'], ['Stale', '오래됨'], ['Archived', '보관됨'], ['Mystery', 'Mystery'], ['draft', 'draft']];
  for (const language of ['ko', 'en']) for (const [raw, korean] of states) await t.test(`${language}/${raw}`, async (context) => {
    const detail = { change_id: 'c1', locator: 'Archived', revision: 'Draft', state: raw, documents: [{ document_id: 'proposal', path: 'Validated', editable: true }], storyboard_frames: [] };
    const frozen = JSON.stringify(detail), source = 'Draft\nValidated\nNeedsReview\nApproved\nStale\nArchived\nPath\nRole';
    const root = await mountedPm(context, { language, apiRequest: (path) => {
      path = path.split('?')[0];
      if (path === '/changes') return { changes: [detail] };
      if (path === '/changes/c1') return detail;
      if (path.includes('/documents/')) return { document_id: 'proposal', source, path: 'Validated' };
    } });
    const expected = language === 'ko' ? korean : raw, option = root.querySelector('.pm-change-select').options.find((node) => node.value === 'c1'), badge = root.querySelector('.pm-revision-bar').querySelector('.pm-state');
    assert.equal(option.textContent, `Archived · ${expected}`); assert.equal(option.value, 'c1');
    assert.equal(badge.textContent, expected); assert.equal(badge.className, `pm-state ${raw.toLowerCase()}`);
    assert.equal(root.querySelector('.pm-revision-bar').querySelector('strong').textContent, 'Archived');
    assert.equal(root.querySelector('.pm-revision-bar').querySelector('code').title, 'Draft');
    assert.equal(root.querySelector('textarea').value, source, 'User source is never a state-label consumer');
    assert.equal(JSON.stringify(detail), frozen, 'No API response mutation');
  });
});

test('round14 planning-files draft label is scoped to its owned cell with raw attributes', async (t) => {
  for (const language of ['ko', 'en']) await t.test(language, async (context) => {
    const documents = ['draft', 'Mystery', 'Draft'].map((state, index) => ({ document_id: `d${index}`, path: `${state}.md`, state, editable: index === 0 }));
    const detail = { change_id: 'c1', locator: 'draft', revision: 'draft', state: 'Draft', documents, storyboard_frames: [] }, frozen = JSON.stringify(detail);
    const root = await mountedPm(context, { tab: 'code', language, apiRequest: (path) => {
      path = path.split('?')[0]; if (path === '/changes') return { changes: [detail] }; if (path === '/changes/c1') return detail;
    } });
    const table = root.querySelector('.pm-card-grid').querySelector('.pm-compact-table'), rows = table.querySelector('tbody').children;
    assert.deepEqual(table.querySelectorAll('th').map((node) => node.textContent), language === 'ko' ? ['경로', '상태', '역할'] : ['Path', 'State', 'Role']);
    rows.forEach((row, index) => {
      const raw = documents[index].state, cell = row.children[1];
      assert.equal(row.children[0].textContent, `${raw}.md`, 'Path bytes remain raw');
      assert.equal(cell.textContent, language === 'ko' && raw === 'draft' ? '초안' : raw);
      assert.equal(cell.getAttribute('data-state'), raw); assert.equal(cell.title, raw);
      assert.equal(row.children[2].textContent, language === 'ko' ? index === 0 ? '기획 원본' : '읽기 전용' : index === 0 ? 'Planning source' : 'Read only');
    });
    assert.equal(JSON.stringify(detail), frozen);
  });
});

test('round15 narrowing restores owned summary even when native-closed controls still report rectangles', async (t) => {
  const media = round14Media(false), root = await mountedPm(t, { media });
  const revision = round14Disclosure(root, 'revision'), summary = revision.querySelector('summary');
  const approve = findButton(root, 'Approve revision');
  // The installed browser retained layout rectangles for this closed native
  // details descendant. Supply that observation independently of open state;
  // browser keyboard receipts, not this adapter, establish actual visibility.
  const rect = { left: 0, top: 0, right: 44, bottom: 44, width: 44, height: 44 };
  for (const node of [root, ...root.querySelectorAll('*')]) node.getClientRects = () => node.isConnected ? [rect] : [];
  revision.open = true; await revision.trigger('toggle');
  assert.equal(revision.open, true, 'Focus starts in an explicitly opened disclosure');
  approve.focus();
  // Observe the actual closed-descendant condition before the media lifecycle
  // recovers it; resizing alone must preserve an explicitly open user intent.
  revision.open = false; await revision.trigger('toggle');
  await media.resize(true);
  assert.equal(revision.open, false, 'The focused action now belongs to a closed native disclosure');
  assert.equal(approve.getClientRects().length, 1, 'Nonzero layout rectangles do not make a closed descendant a valid focus destination');
  assert.equal(summary.getClientRects().length, 1, 'The owned summary remains a supplied visible destination');
  assert.ok(document.activeElement === summary, `Focus must move to its revision summary; actual ${document.activeElement?.tag}: ${document.activeElement?.textContent}`);
  assert.deepEqual(summary.focusCalls.at(-1), { preventScroll: true });
});

test('round15 owned Code Files empty and coverage explanations localize without translating raw document data', async (t) => {
  const messages = ['No managed tickets have been handed off.', 'Specified, linked, implemented, tested, accepted, and waived remain independent.'];
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    // Unknown document states may equal owned English copy; they remain data.
    const detail = { change_id: 'c1', locator: 'English fixture', revision: 'Unknown revision', state: 'Draft', storyboard_frames: [], documents: messages.map((state, index) => ({ document_id: `authored-${index}`, path: `English fixture/Unknown-${index}.md`, state, editable: true })) };
    const frozen = JSON.stringify(detail);
    const root = await mountedPm(context, { tab: 'code', language, apiRequest: (path) => {
      path = path.split('?')[0];
      if (path === '/changes') return { changes: [detail] };
      if (path === '/changes/c1') return detail;
    } });
    const ticketCard = root.querySelectorAll('section.pm-readable-card').find((node) => node.querySelector('h2')?.textContent === pmText('Handoff tickets', {}, language));
    assert.ok(ticketCard, 'Exercise the actual empty handoff consumer');
    assert.deepEqual([ticketCard.querySelector('p').textContent, root.querySelector('.pm-coverage').querySelector('p').textContent], language === 'ko'
      ? ['아직 전달된 관리 티켓이 없습니다.', '명세, 연결, 구현, 테스트, 인수, 면제는 각각 별도로 판단합니다.'] : messages);
    const rows = root.querySelector('.pm-compact-table').querySelector('tbody').children;
    rows.forEach((row, index) => {
      assert.equal(row.children[0].textContent, detail.documents[index].path, 'Authored English path remains raw');
      assert.equal(row.children[1].textContent, messages[index], 'Unknown document state is not an owned-copy consumer');
      assert.equal(row.children[1].getAttribute('data-state'), messages[index]);
    });
    assert.equal(JSON.stringify(detail), frozen, 'API fixture bytes stay unchanged');
  });
});

test('round15 owned missing-baseline explanations localize without translating authored requirement source', async (t) => {
  const messages = ['Current baseline · new capability', 'No baseline specification exists yet.'];
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const documents = messages.map((source, index) => ({ document_id: `spec:english-fixture-${index}`, path: `English fixture/spec-${index}.md`, source, editable: true }));
    const detail = { change_id: 'c1', locator: 'English fixture', revision: 'Unknown revision', state: 'Draft', storyboard_frames: [], documents };
    const frozen = JSON.stringify(detail);
    const root = await mountedPm(context, { tab: 'requirements', language, apiRequest: (path) => {
      const pathname = path.split('?')[0];
      if (pathname === '/changes') return { changes: [detail] };
      if (pathname === '/changes/c1') return detail;
      if (pathname.includes('/baselines/')) return { exists: false };
      const id = decodeURIComponent(pathname.split('/documents/')[1] || '');
      const doc = documents.find((item) => item.document_id === id);
      if (doc) return { ...doc };
    } });
    const comparisons = root.querySelectorAll('.pm-requirement-delta');
    assert.equal(comparisons.length, 2, 'Exercise the actual missing-baseline comparison consumers');
    const expected = language === 'ko' ? ['현재 기준 · 새 기능', '아직 기준 명세가 없습니다.'] : messages;
    assert.deepEqual(comparisons.map((node) => [node.children[1].querySelector('h3').textContent, node.children[1].querySelector('p').textContent]), [expected, expected]);
    comparisons.forEach((node, index) => assert.equal(node.children[0].querySelector('p').textContent, messages[index], 'Authored source equal to owned English copy remains raw'));
    assert.deepEqual(root.querySelectorAll('.pm-source-details').map((node) => node.querySelector('pre').textContent), messages);
    assert.deepEqual(root.querySelectorAll('.pm-card-heading').map((node) => node.querySelector('code').textContent), documents.map((doc) => doc.path));
    assert.equal(JSON.stringify(detail), frozen, 'API fixture bytes stay unchanged');
  });
});

test('round16 Requirements metadata folds at every width without folding authored Proposed or baseline content', async (t) => {
  const source = '# Authored requirement title\n\nAuthored Proposed text stays raw.\n';
  const baseline = '# Existing baseline title\n\nAuthored Current baseline text stays raw.\n';
  const path = 'English fixture/keep-spec.md';
  for (const language of ['en', 'ko']) for (const compact of [true, false]) await t.test(`${language}, compact=${compact}`, async (context) => {
    const media = round14Media(compact), writes = [];
    const detail = { change_id: 'c1', locator: 'English fixture', revision: 'Unknown revision', state: 'Draft', storyboard_frames: [], documents: [{ document_id: 'spec:keep', path, editable: true }] };
    const frozen = JSON.stringify(detail);
    const root = await mountedPm(context, { tab: 'requirements', language, media, apiRequest: (requestPath, options) => {
      const pathname = requestPath.split('?')[0];
      if (['POST', 'PATCH', 'DELETE'].includes(options?.method)) writes.push(pathname);
      if (pathname === '/changes') return { changes: [detail] };
      if (pathname === '/changes/c1') return detail;
      if (pathname.includes('/baselines/')) return { exists: true, source: baseline };
      if (pathname.includes('/documents/')) return { document_id: 'spec:keep', source, path };
    } });
    const comparison = root.querySelector('.pm-requirement-delta'), metadata = root.querySelector('.pm-card-heading');
    assert.equal(comparison.children[0].querySelector('h3').textContent, pmText('Proposed', {}, language));
    assert.equal(comparison.children[1].querySelector('h3').textContent, pmText('Current baseline', {}, language));
    assert.equal(comparison.children[0].querySelector('p').textContent, source, 'Keep every authored proposed heading and paragraph in order');
    assert.equal(comparison.children[1].querySelector('p').textContent, baseline, 'Keep the complete baseline readable');
    assert.equal(root.querySelector('.pm-source-details').querySelector('pre').textContent, source);
    assert.equal(metadata.querySelector('h2').textContent, 'keep');
    const code = metadata.querySelector('code'), edit = findButton(metadata, pmText('Edit requirements', {}, language));
    assert.equal(code.textContent, path); assert.equal(code.title, path);
    const owner = metadata.closest('details');
    assert.ok(owner, 'Owned capability/path/delta/Edit metadata belongs to a native secondary disclosure');
    assert.ok(owner.querySelector('summary')?.textContent.trim(), 'The disclosure has a meaningful native summary');
    assert.equal(owner.open, false, 'Secondary metadata starts closed at every width');
    assert.equal(owner.contains(comparison), false, 'Proposed and baseline content stay outside secondary-control closure');
    const before = { code, edit, delta: metadata.querySelector('.pm-count').textContent, handlers: [...edit.listeners.get('click')] };
    owner.open = true; await owner.trigger('toggle');
    await media.resize(!compact);
    assert.equal(owner.open, true, 'Explicitly opened metadata remains open across width changes');
    assert.equal(metadata.querySelector('code'), before.code); assert.equal(findButton(metadata, pmText('Edit requirements', {}, language)), before.edit);
    assert.equal(metadata.querySelector('.pm-count').textContent, before.delta);
    assert.deepEqual(edit.listeners.get('click'), before.handlers, 'Keep the original Edit handler exactly once');
    assert.equal(root.querySelectorAll('button').filter((node) => node.textContent === edit.textContent).length, 1);
    await edit.trigger('click'); await settlePm();
    assert.equal(root.querySelector('textarea').value, source, 'The existing Edit action still opens the same source');
    findButton(root, pmText('Back to requirements', {}, language));
    assert.equal(writes.length, 0, 'Folding and opening an editor do not save or dispatch');
    assert.equal(JSON.stringify(detail), frozen);
  });
});
