import { validateWorkbookFilename } from './exchange.js';

export function createPmApi(hostApi, projectId) {
  let activeProjectId = projectId;
  const operationId = () => crypto.randomUUID().replaceAll('-', '');
  const envelope = (changeId, revision, payload = {}, op = null) => ({ project_id: activeProjectId, change_id: changeId, expected_revision: revision || null, client_operation_id: op, payload });
  const query = () => `?project_id=${encodeURIComponent(activeProjectId)}`;
  const request = (path, options = {}) => hostApi.request(path, options);
  return {
    setProjectId(value) { activeProjectId = value; },
    capabilities: (signal) => request('/capabilities', { signal }),
    changes: (signal) => request(`/changes${query()}`, { signal }),
    change: (id, signal) => request(`/changes/${id}${query()}`, { signal }),
    document: (id, doc, signal) => request(`/changes/${id}/documents/${encodeURIComponent(doc)}${query()}`, { signal }),
    baseline: (id, capability, signal) => request(`/changes/${id}/baselines/${encodeURIComponent(capability)}${query()}`, { signal }),
    createSpec: (id, revision, capability, title) => request(`/changes/${id}/specs`, { method: 'POST', body: { ...envelope(id, revision, {}, operationId()), capability, title } }),
    wbs: (id, signal) => request(`/changes/${id}/wbs${query()}`, { signal }),
    managedTickets: (id, signal) => request(`/changes/${id}/managed-tickets${query()}`, { signal }),
    coverage: (id, revision, signal) => request(`/changes/${id}/coverage${query()}&revision=${encodeURIComponent(revision)}`, { signal }),
    acceptancePreview: (ticket, taskRevision, testedCommit) => request(`/tickets/${encodeURIComponent(ticket)}/acceptance/preview${query()}&task_revision=${encodeURIComponent(taskRevision)}&tested_commit=${encodeURIComponent(testedCommit)}`),
    accept: (ticket, taskRevision, testedCommit, confirmationToken) => request(`/tickets/${encodeURIComponent(ticket)}/acceptance`, { method: 'POST', body: { project_id: activeProjectId, task_revision: taskRevision, tested_commit: testedCommit, confirmation_token: confirmationToken, reviewer_label: 'local operator' } }),
    verify: (id, ticket, taskRevision, procedureId, runId) => request(`/changes/${id}/verification`, { method: 'POST', body: envelope(id, null, { ticket_identifier: ticket, task_revision: taskRevision, procedure_id: procedureId, run_id: runId, artifact_refs: [] }, operationId()) }),
    create: (workspacePath, locator, title) => request('/changes', { method: 'POST', body: { project_id: activeProjectId, workspace_path: workspacePath, locator, title } }),
    claim: (id, workspacePath) => request(`/changes/${id}/workspace/claim`, { method: 'POST', body: envelope(id, null, { workspace_path: workspacePath }) }),
    repair: (id, workspacePath) => request(`/changes/${id}/workspace/repair`, { method: 'POST', body: envelope(id, null, { workspace_path: workspacePath }) }),
    convertLegacy: (sourceTicketId, workspacePath, locator, title) => request('/legacy-conversions', { method: 'POST', body: { project_id: activeProjectId, source_ticket_id: sourceTicketId, workspace_path: workspacePath, locator, title } }),
    saveDocument: (id, revision, doc, source) => request(`/changes/${id}/documents/${encodeURIComponent(doc)}`, { method: 'PATCH', body: envelope(id, revision, { operations: [{ op: 'replace_source', source }] }, operationId()) }),
    createSemantic: (id, revision, doc, kind, title) => request(`/changes/${id}/documents/${encodeURIComponent(doc)}`, { method: 'PATCH', body: envelope(id, revision, { operations: [{ op: 'create_semantic', kind, title }] }, operationId()) }),
    patchStoryboard: (id, revision, operation) => request(`/changes/${id}/documents/ux`, { method: 'PATCH', body: envelope(id, revision, { operations: [operation] }, operationId()) }),
    patchWbs: (id, revision, operations) => request(`/changes/${id}/wbs`, { method: 'PATCH', body: envelope(id, revision, { operations }, operationId()) }),
    previewWbs: (id, revision, operations) => request(`/changes/${id}/wbs/preview`, { method: 'POST', body: envelope(id, revision, { operations }) }),
    validate: (id, revision) => request(`/changes/${id}/validate`, { method: 'POST', body: envelope(id, revision) }),
    beginExternal: (id, revision) => request(`/changes/${id}/external-edit/begin`, { method: 'POST', body: envelope(id, revision) }),
    endExternal: (id) => request(`/changes/${id}/external-edit/end`, { method: 'POST', body: envelope(id, null) }),
    approve: (id, revision, token) => request(`/changes/${id}/approvals`, { method: 'POST', body: { ...envelope(id, revision), confirmation_token: token, reviewer_label: 'local operator' } }),
    handoffPreview: (id, revision) => request(`/changes/${id}/handoffs/preview`, { method: 'POST', body: envelope(id, revision) }),
    handoff: (id, revision, previewToken, confirmationToken) => request(`/changes/${id}/handoffs`, { method: 'POST', body: { ...envelope(id, revision, {}, operationId()), preview_token: previewToken, confirmation_token: confirmationToken } }),
    export: (id, revision, format) => request(`/changes/${id}/exports`, { method: 'POST', body: { ...envelope(id, revision), format } }),
    importPreview: (id, revision, filename, encoded) => request(`/changes/${id}/imports/preview`, { method: 'POST', body: envelope(id, revision, { filename: validateWorkbookFilename(filename), base64: encoded }, operationId()) }),
    importApply: (id, revision, importId, digest, resolutions, applyOperationId) => request(`/changes/${id}/imports/${importId}/apply`, { method: 'POST', body: { ...envelope(id, revision, {}, applyOperationId), preview_digest: digest, resolutions } }),
    startPlanning: (id, revision, action) => {
      const op = operationId();
      return { operationId: op, promise: request(`/changes/${id}/planning-actions`, { method: 'POST', body: { ...envelope(id, revision, {}, op), ...action } }) };
    },
    applyPlanning: (id, revision, proposalId, proposalDigest) => request(`/changes/${id}/planning-actions/${proposalId}/apply`, { method: 'POST', body: { ...envelope(id, revision, {}, operationId()), proposal_digest: proposalDigest } }),
    operation: (id) => request(`/operations/${id}`),
    cancelOperation: (id) => request(`/operations/${id}/cancel`, { method: 'POST', body: {} }),
    evidencePreview: (id, revision, payload) => request(`/changes/${id}/manual-evidence/preview`, { method: 'POST', body: { project_id: activeProjectId, expected_revision: revision, ...payload } }),
    evidenceSubmit: (id, revision, payload, confirmationToken, op = operationId()) => request(`/changes/${id}/manual-evidence`, { method: 'POST', body: { project_id: activeProjectId, expected_revision: revision, client_operation_id: op, ...payload, confirmation_token: confirmationToken } }),
    waiverPreview: (id, revision, semanticId, reason, reviewerLabel) => request(`/changes/${id}/coverage/waivers/preview`, { method: 'POST', body: { project_id: activeProjectId, expected_revision: revision, semantic_id: semanticId, reason, reviewer_label: reviewerLabel } }),
    createWaiver: (id, revision, payload, token) => request(`/changes/${id}/coverage/waivers`, { method: 'POST', body: { project_id: activeProjectId, expected_revision: revision, client_operation_id: operationId(), ...payload, confirmation_token: token } }),
    revokeWaiver: (id, revision, waiverId, reason, reviewerLabel) => request(`/changes/${id}/coverage/waivers/${waiverId}/revoke`, { method: 'POST', body: { project_id: activeProjectId, expected_revision: revision, client_operation_id: operationId(), reason, reviewer_label: reviewerLabel } }),
    archivePreview: (id, revision) => request(`/changes/${id}/archive/preview`, { method: 'POST', body: envelope(id, revision) }),
    archive: (id, revision, archiveId, token) => request(`/changes/${id}/archive/${archiveId}`, { method: 'POST', body: { ...envelope(id, revision, {}, operationId()), confirmation_token: token } }),
    download: hostApi.download,
  };
}
