# Plan Attack - Frontier 004

## Verdict

PASS after two binding amendments and an independent amendment-only recheck.

## Findings Closed

- Replaced loopback-as-human approval with injected production deny-all issue-plan authority; activation depends on
  the worker-inaccessible managed surface in 009.
- Fixed `issue_revision` to Frontier 001 `source.revision` and added closed evidence-producer authority.
- Fixed delivery tables to the existing workflow state DB and made pending projection non-dispatchable.
- Bound Learn-to-Done, active/reacquired run lease, existing completion token, cleanup, and lease release order.
- Derived promotion service/environment only from attested route/profile and froze uncertain outcome retention.
- Moved card CAS/outbox before Core integration, enumerated all bypass paths, and added exact slice dependencies.
- Removed speculative permit/API/DSL surfaces and froze hot-reload generation semantics.

Detailed independent evidence is preserved during the run at:

- `/private/tmp/f004-plan-attack-result.md`
- `/private/tmp/f004-plan-attack-fast-result.md`
- `/private/tmp/f004-plan-recheck-result.md`

No live mutation or external action was authorized by the planning pass.
