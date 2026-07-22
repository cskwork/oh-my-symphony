# Surfaced Requirements - Frontier 004

## 2026-07-22 - independent critic baseline

- Status: open. Card state is an untrusted requested projection; no AIDT dispatch or side-effect authority may rely
  on it without a matching durable journal decision. Planned RED: every skipped edge and direct Done attempt.
- Status: open. Evidence freshness is causal and identity-bound, including issue revision, plan hash, stage epoch,
  workspace/SHA chain, command/result/time, and side-effect review. Planned RED: mutation matrix for each field.
- Status: open. Infrastructure approval is not issue-plan approval. Planned RED: cross-purpose/revision/hash/child
  replay denial and exact idempotent approval replay.
- Status: open. Three low-confidence attempts survive restart and a fourth worker is never dispatched. Planned RED:
  restart between attempts and manual Human Review rollback.
- Status: open. Serialization spans Merge, Deploy, and Dev QA for the same service/environment and retains uncertain
  ownership across restart. Planned RED: same-key race, different-key concurrency, unknown Deploy retention.
- Status: open. SQLite/card split persistence must fail closed at either crash boundary and must not overwrite a
  concurrent operator revision. Planned RED: decision-before-projection, projection-before-decision, and CAS races.
- Status: open. Board, detail, and history must expose the same durable reason and redact bounded command/detail
  fields. Planned RED: restart-stable parity and redaction.
- Status: open. Neither loopback reachability nor an actor string proves human intent. Production issue-plan
  authority is deny-all until Frontier 009 provides a worker-inaccessible operator adapter. Planned RED: worker/card/
  generic API self-approval is impossible while an exact fixture authority can prove revision/hash binding.
- Status: open. Authoritative evidence requires a closed producer capability; typed JSON alone is forgeable.
  Planned RED: worker-forged Local-QA/Git/Jenkins/Dev-QA facts remain denied before Frontiers 005-008.
- Status: open. A committed decision is non-dispatchable until card projection CAS is acknowledged, and startup must
  reconcile both crash directions before polling. Planned RED: journal-ahead, card-ahead, concurrent edit.
- Status: open. Learn-to-Done and active-run lease verification share the existing state DB transaction; cleanup uses
  only the Frontier 003 completion seam and never trusts Done card state. Planned RED: active/reacquired/no-lease
  completion fixtures.
- Status: open. Fence identity is derived from the attested route and closed profile, never from an action payload.
  Planned RED: service/environment alias and same-target-different-spelling races fail before acquisition.
- Status: open. Material hot reload cannot reinterpret evidence, approval, or a held fence under a new generation.
  Planned RED: equivalent reload parity and graph/environment/authority drift closure.
- Status: open. Every state writer is outside the authority seam; the next reconcile must deny an illegal skip before
  destination dispatch or cleanup. Planned RED: raw file, board API, tracker/CLI/TUI-equivalent, worker exit, and
  restart-residue attempts.
