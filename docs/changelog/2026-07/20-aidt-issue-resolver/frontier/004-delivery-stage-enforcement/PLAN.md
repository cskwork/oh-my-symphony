# PLAN - Frontier 004 Delivery Stage Enforcement

## Approval

- Status: auto-approved under the user's instruction to accept the sustainable plan.
- Route: GREENFIELD inside the existing isolated run worktree.
- Source/base: `origin/dev`; target/integration: `dev`; run branch:
  `run/symphony-aidt-orchestrator-20260720`.
- Generic merge, live AIDT mutation, Git push, Jenkins, Jira writes, and dev QA remain forbidden in this frontier.
- Max critic/fixer iterations per slice: 3.

## Chosen Design

Use one deep `AidtDeliveryController` module with three entry points: `publish(config)`, `apply(generation, action)`,
and `snapshot(generation, identifier)`. A closed tagged action union carries card observations, typed evidence,
issue-plan approval, transition requests, and future side-effect permit requests. A workflow-local SQLite journal is
authoritative; file cards and HTTP payloads are projections. The stage graph and validators are closed AIDT v1
code, not a general policy DSL.

Ask Matt design-it-twice compared minimal, caller-optimized, and extensible interfaces. All converged on the same
three-entry seam. The extensible declarative-policy variant was rejected because only one real policy exists; it
would create speculative interface area without leverage. The selected hybrid keeps locality in one module and
lets Frontiers 005-008 submit typed facts without authorizing themselves.

## Frozen Safety Decisions

1. Canonical forward flow is `Intake -> Route -> Plan -> Plan Approval -> Worktree -> Build -> Review -> Local QA ->
   Commit -> Merge -> Deploy -> Dev QA -> Learn -> Done`.
2. Human Review, Blocked, and Cancelled are terminal. Specialized failure reasons are durable reason codes; adding
   many one-off lanes is deferred unless a later UI requirement proves they add value.
3. Freshness is causal by default: current issue revision, plan hash, stage epoch, predecessor facts, workspace/SHA
   chain, and non-future timestamps. No arbitrary age TTL is invented.
4. Exact duplicate facts are idempotent; conflicting current facts are ambiguous and fail closed.
5. Low confidence means three Plan attempts total per issue revision. A plan-hash change does not reset the count; a
   new issue revision starts a new epoch and requires a new plan and approval.
6. The promotion fence is held from Commit-to-Merge admission through successful Dev-QA-to-Learn. Unknown Deploy or
   failed Dev QA retains it for explicit Human Review; it is never freed by a blind TTL retry.
7. The controller validates evidence shape, freshness, identity, and relationships only. Future adapters own the
   real commands and observations.
8. SQLite authority and card projection cannot share a physical transaction. Journal decision/outbox plus
   expected-state card CAS and startup reconciliation make crashes fail closed.

## Worker-Sized Ticket Sequence

### 004a - Contract and exact profile

- Owns: `src/symphony/aidt_delivery/{__init__,contract}.py`, focused contract tests, and only the minimal example/test
  fixtures needed to prove default-off and exact lanes.
- RED: closed config, exact graph, typed scalar bounds, generic auto-commit/merge rejection, no work before approval.
- GREEN: immutable public types and strict loader; no persistence or Core integration.

### 004b - Durable journal

- Owns: `src/symphony/aidt_delivery/store.py` and store-focused tests.
- RED: append/idempotency/conflict, CAS revision, reopen parity, corrupt/truncated record denial, crash boundaries.
- GREEN: bounded SQLite schema, `BEGIN IMMEDIATE`, canonical digests, append-only facts/decisions, materialized status.

### 004c - Evidence and approval evaluator

- Owns: `src/symphony/aidt_delivery/evaluator.py` and evaluator-focused public-interface tests.
- RED: every missing/failed/stale/ambiguous/mismatched fact; approval replay and cross-revision/hash/child reuse;
  exact SHA and side-effect relationships.
- GREEN: closed stage requirement table and stable sanitized reasons; no executor adapters.

### 004d - Reducer, attempts, and rewinds

- Owns: `src/symphony/aidt_delivery/controller.py` reducer paths and transition-matrix tests.
- RED: every skip/reverse edge, exact adjacent flow, Review/Local-QA rewind, three attempts and no fourth, restart.
- GREEN: journal-authoritative stage epochs and fresh-context decisions.

### 004e - Promotion fence and completion authority

- Owns: controller/store fence paths, fence/concurrency tests, and the existing AIDT worktree completion seam only.
- RED: one same-key winner, different-key concurrency, restart retention, uncertain outcome retention, exact release,
  no cleanup without current completed evidence.
- GREEN: durable service/environment ownership spanning Merge through Dev QA; no Git/Jenkins action.

### 004f - Runtime publication and orchestrator barrier

- Owns: `src/symphony/aidt_delivery/runtime.py`, narrow `orchestrator/core.py` integration, and focused lifecycle tests.
- RED: card edits cannot bypass journal before dispatch/rebuild/terminal cleanup; reload is atomic and default-off
  parity remains exact.
- GREEN: publish one generation, reconcile at every AIDT admission/transition/cleanup path, keep generic workflows on
  their existing Markdown contract.

### 004g - Card CAS and minimal operator/API projection

- Owns: narrow file-tracker expected-state mutation, controller projection adapter, loopback plan-approval endpoint,
  board/detail/history projection, and focused tracker/web tests.
- RED: concurrent operator edit is not overwritten; board/detail reasons match and survive restart; approval requires
  expected revision/hash; commands and details are redacted.
- GREEN: SQLite remains authority; no generic evidence-write HTTP endpoint.

### 004h - Operator profile and prompt coverage

- Owns: `examples/WORKFLOW.aidt.example.md`, required stage prompt aliases/files, workflow/example/doctor tests, and
  README only if needed.
- RED: exact lane order and prompt coverage absent in the existing example.
- GREEN: full default-off profile on port 9918, generic auto-merge disabled, all external adapters still absent.

### 004v - Independent rollup verification

- Owns no new product behavior. Re-read prose, surface missing requirements as RED tests, run focused/affected/full
  suites, Ruff, Pyright, structure/whitespace, doctor, restart/concurrency matrices, and write final evidence.
- A finding opens a bounded correction ticket; no finding is hidden in the rollup.

## Required Proof

- Primary: `pytest -q tests/test_aidt_stage_gates.py tests/test_workflow.py`.
- Focused: AIDT delivery contract/store/controller, example, orchestrator lifecycle, tracker CAS, and web API tests.
- Regression: all AIDT routing/worktree tests, orchestrator suite, then full repository parity.
- Static: Ruff, Pyright, structure and whitespace checks used by Frontier 003.
- Runtime: restart the same workflow/SQLite directory; race same and different service/environment keys; run example
  doctor on a temporary copy.

## Rejected Alternatives

- Expand generic Markdown contract validation: it is prompt-shaped, post-hoc, and bypassed by other state writers.
- Treat card state as authority: direct worker/operator edits would authorize side effects.
- Reuse `_IssueDebug.rewind_count`: it is generic process memory and resets on restart.
- General declarative policy/validator DSL: one real AIDT graph does not justify the interface.
- Per-lane concurrency caps: they do not serialize one service across Merge, Deploy, and Dev QA.
- Auto-expiring uncertain deployment ownership: can duplicate an external side effect.

## Binding Amendment 1 - Plan Attack Closure

This amendment supersedes every conflicting statement above.

### 1. Human approval and evidence authority

1. `IssuePlanApprovalAuthority` is an injected interface with a production `DenyAllIssuePlanApprovalAuthority`
   default. Frontier 004 exposes approval/history as read-only projections; it does not ship a loopback approval
   mutation endpoint. Fixture tests may install an exact operator test adapter, but that proves policy binding rather
   than live human identity. Ticket 009 owns the worker-inaccessible managed operator surface required for activation.
2. A worker, card edit, prompt, generic HTTP client, infrastructure approval, actor string, CSRF token, or nonce alone
   cannot create an issue-plan approval fact. The authority must attest the exact coordinator issue, child, canonical
   issue revision, plan hash, purpose `issue_plan`, and one idempotent decision.
3. `EvidenceProducerAuthority` is likewise closed and deny-all for producer kinds not yet implemented. Intake,
   routing, and worktree evidence is admitted only from the existing closed in-process observers. Local QA, Git
   promotion, Jenkins, and Dev-QA evidence stays denied until Frontiers 005-008 register their reviewed adapters.
   Cards, workers, and HTTP surfaces never receive a generic evidence-write capability.

### 2. Canonical issue revision and drift

1. `issue_revision` is exactly the lowercase SHA-256 `source.revision` produced by Frontier 001
   `jira_intake.build_source_snapshot`. It digests the immutable structured Jira source snapshot and excludes local
   state, notes, delivery projection, approval, and `updated_at`. The child must resolve it through the freshly
   attested coordinator/child route pair; missing, malformed, or unequal revisions are non-dispatchable.
2. Revision drift before Merge returns to Plan, opens a new issue epoch, invalidates the plan/approval/downstream
   facts, and requires new approval. Drift at or after Merge enters Human Review with the promotion fence retained.
3. Merge failure releases the fence only with authoritative evidence that the remote target did not change. Deploy
   unknown/failure and Dev-QA failure remain fenced until an operator-resolution authority records the observed
   external outcome. Cancellation never silently releases uncertain ownership.

### 3. SQLite, projection, and crash ordering

1. Delivery-owned namespaced tables live in the existing workflow `.symphony/state.db` returned by
   `registry_path_for_workflow`. They do not add AIDT fields to generic `RunRecord` rows. The delivery store queries
   the active run lease and writes the final delivery decision in the same `BEGIN IMMEDIATE` transaction.
2. Accepted decisions first enter `decision_pending_projection`, which is non-dispatchable. The projection adapter
   performs an expected-state plus expected-source-revision CAS on the file card. Only an acknowledged projection
   becomes dispatchable. A concurrent edit is preserved, records `tracker_changed`, and waits or enters Human Review.
3. Startup drains/reconciles pending projection outbox rows before candidate dispatch. A journal-ahead card is healed
   idempotently; a card-ahead journal is repaired or escalated and never dispatches. Crash tests kill on both sides of
   journal commit and projection acknowledgement and assert reopened public status/card/API behavior.

### 4. Completion and active run lease ordering

1. While the owning run lease is active, the controller transaction records Learn-to-Done, binds the final-transition
   identity to the exact issue/run/attempt/route/workflow/ready-manifest tuple, and derives the existing
   `CompletionAuthorization`. The existing Frontier 003 `CompletionAuthority.verify` and worktree removal seam are
   the only cleanup implementation; only after handled cleanup is the run lease released.
2. Restart never manufactures authority from a Done card. It may use the existing exact `reacquired` attempt/lease
   path only when the same durable final-transition identity is re-attested; otherwise the worktree remains deny-all
   preserved. Frontier 004 tests this with temporary Git fixtures only and performs no live AIDT mutation.
3. Until Frontiers 005-009 provide real evidence and human authority adapters, the default-off production profile
   cannot reach this path. Fixture completion proves the contract, not live activation.

### 5. Corrected ticket dependencies and ownership

- `004a` owns only contract/profile types and focused `tests/test_aidt_delivery_contract.py`; it proves Plan Approval
  is declared non-dispatchable, not the runtime no-work guarantee.
- `004b` is blocked by `004a` and owns `store.py` plus store tests in the shared state DB.
- `004c` is blocked by `004a,004b` and owns evidence/approval evaluation plus authority adapters and focused tests.
- `004d` is blocked by `004c` and owns the reducer/controller transition matrix and durable attempts.
- `004e` is blocked by `004d`; it owns new `fence.py` and `completion.py` plus narrow controller composition and
  temporary-fixture tests. It does not reopen `store.py` ownership.
- `004g` is blocked by `004d,004e` and runs before Core integration. It owns card expected-state/revision CAS,
  projection/outbox acknowledgement, read-only board/detail/history data, and focused tracker/API tests.
- `004f` is blocked by `004g` and owns runtime publication plus all Core admission/transition/terminal barriers. The
  no-work-before-approval behavior belongs here.
- `004h` is blocked by `004a,004f`; it alone owns `tests/test_workflow.py`, the AIDT example, and prompt/doctor proof.
- `004v` is blocked by all prior slices and owns aggregate verification/evidence only.

Every slice must leave its dependency-closed focused suite green. No two active workers own the same product or test
file; shared controller composition is sequential and explicitly handed off. The primary
`tests/test_aidt_stage_gates.py tests/test_workflow.py` command is the 004v rollup gate, not a claim that every early
slice can independently satisfy the final matrix.

## Binding Amendment 2 - Independent Recheck Closure

This amendment adds the remaining constraints from the second independent attack and supersedes conflicting text.

1. The Frontier 004 action union contains observations, evidence facts, approval facts, and transition requests only.
   It does not expose future side-effect permit requests. Frontiers 005-008 add their exact permit/producer actions
   with the real adapters that justify those seams.
2. Fence `service` is derived only from the current freshly attested AIDT route child. Fence `environment` is derived
   only from the closed enabled delivery profile; Frontier 004 accepts `fixture` and the reviewed literal `aidt-dev`.
   Caller payload values are equality assertions, never selectors. Alias/case/whitespace mismatches fail before lock
   acquisition.
3. Successful Dev-QA-to-Learn releases the promotion fence only after all correlated external evidence is current.
   It does not authorize worktree cleanup. Only the later committed Learn-to-Done decision mints completion authority.
4. `004b` proves only SQLite journal atomicity, canonical idempotency/conflict, corrupt-row denial, and durable
   pending-projection/outbox rows. Cross-resource crash windows are owned by `004g` and re-run through fresh
   orchestrator startup in `004f`; unresolved/corrupt SQLite or outbox state closes all AIDT admission and reports
   degraded health with no generic fallback.
5. The explicit AIDT barrier sites are dispatch eligibility, observed worker transition, board state mutation,
   CLI/TUI/file-tracker mutation as observed at reconciliation, worker exit, terminal reconcile, startup terminal
   cleanup, and worktree removal. A card state is always a requested projection; no destination backend or cleanup
   begins until the matching journal decision is committed and projection-acknowledged.
6. Material delivery-config reload never reinterprets history. Byte-equivalent canonical configuration may publish
   an equivalent generation. Graph/environment/authority/safety changes close new admission; existing epochs and
   fences stay bound to their original generation and require identical resume or explicit Human Review recovery.
7. `004c` tests the internal evaluator contract honestly. The public `apply` behavior begins in `004d`; 004c does not
   claim interface-level transition proof. Review/Local-QA rewind supersedes downstream facts but never increments
   the low-confidence Plan attempt counter.
8. Frontier 004 HTTP scope is read-only delivery status/history. Approval mutation, evidence submission, permit
   issuance, fence override, recovery, and generic transition authorization remain absent. Frontier 009 may add one
   capability-protected human approval action without changing the controller interface.
