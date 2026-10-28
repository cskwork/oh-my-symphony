export const PLANNING_ACTIONS = [
  ['draft_artifact', 'Draft artifact'], ['refine_section', 'Refine selected section'], ['find_gaps', 'Find gaps'],
  ['review_consistency', 'Review consistency'], ['explain_dependency', 'Explain dependency'], ['propose_wbs', 'Propose WBS'],
  ['update_impacted_artifacts', 'Update impacted artifacts'],
];

export function planningActionRequest(action, values = {}) {
  if (!PLANNING_ACTIONS.some(([id]) => id === action)) throw new Error('Unknown planning action.');
  const documentId = values.documentId || null;
  const targets = [...new Set((values.impactedDocumentIds || []).filter(Boolean))];
  if (!documentId) throw new Error('Choose an artifact.');
  if (action === 'refine_section' && !values.sectionId) throw new Error('Choose a section to refine.');
  if (action === 'explain_dependency' && (!values.dependency?.task_id || !values.dependency?.predecessor_id)) {
    throw new Error('Choose a dependency to explain.');
  }
  if (action === 'review_consistency' && targets.length < 2) throw new Error('Choose at least two impacted artifacts.');
  if (action === 'update_impacted_artifacts' && targets.length < 1) throw new Error('Choose at least one impacted artifact.');
  const scopedTargets = ['draft_artifact', 'refine_section', 'propose_wbs'].includes(action) ? [documentId]
    : action === 'explain_dependency' ? [] : (targets.length ? targets : [documentId]);
  return {
    action,
    document_id: documentId,
    section_id: action === 'refine_section' ? values.sectionId : null,
    dependency: action === 'explain_dependency' ? values.dependency : null,
    instruction: String(values.instruction || ''),
    impacted_document_ids: scopedTargets,
  };
}

export function verificationActions(procedures = []) {
  return procedures.map((procedure) => ({
    ...procedure,
    label: procedure.kind === 'native' ? 'Run verification' : 'Submit completion evidence',
    acceptsReportedResult: procedure.kind === 'manual',
  }));
}

export function evidenceProvenance(receipts = []) {
  const kinds = new Set(receipts.map((item) => item.test_provenance || (item.procedure_kind === 'native' ? 'host_executed' : 'manual_reported')));
  if (kinds.has('host_executed') && kinds.has('manual_reported')) return 'Mixed';
  if (kinds.has('host_executed')) return 'Host executed';
  if (kinds.has('manual_reported')) return 'Manual reported';
  return 'No evidence';
}
