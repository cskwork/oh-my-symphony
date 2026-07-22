# Frontier 003 Worktree Integration Architecture

Date: 2026-07-21
Input: `worktree-provisioning.md`, `frontier003-route-dispatch-contract.md`, ticket 003, and landed commit `e9794e8`.

## Decision

Use a cohesive `symphony.aidt_worktree` package plus two narrow existing seams:

1. routing publishes a validated set of provisionable child identifiers without removing them from the complete
   route-managed set;
2. `WorkspaceManager` delegates owned AIDT paths before any generic directory, hook, or removal behavior;
3. the worker retains its current order: create/resume, delegate-aware `before_run` re-attestation, then backend.

This keeps Git mutation out of routing, keeps generic workspace policy out of AIDT Git code, and gives every initial
or retry attempt the same final barrier.

## Layout options

### A. One `aidt_worktree.py`

Rejected. Card trust, manifest parsing, Git protocol, lifecycle state, and orchestration would form one large module
with unrelated failure modes and weak test seams.

### B. Put AIDT policy in `workspace.py`

Rejected. It would couple every generic workspace to one product catalog and make raw recursive cleanup harder to
audit. The existing manager should only delegate or preserve its current behavior.

### C. Feature package plus typed delegate

Selected:

- `aidt_worktree/contract.py`: closed config, failure/result DTOs, branch/scope/manifest value validation.
- `aidt_worktree/manifest.py`: symlink-safe strict read and same-directory atomic state transitions.
- `aidt_worktree/git_state.py`: fixed Git argv, bounded runner, repository/root/registry/ref snapshots and proofs.
- `aidt_worktree/provisioner.py`: locked create/resume/prepared-recovery/authorized-cleanup state machine.
- `aidt_worktree/runtime.py`: route-card adapter, `WorkspaceManager` delegate, counters, bounded health.
- `aidt_worktree/__init__.py`: narrow public facade without importing orchestrator or tracker implementations eagerly.

No file should combine persistence, Git parsing, and lifecycle decisions. Tests mirror these boundaries and use only
temporary local repositories/remotes.

## Route-to-worker contract

`AidtRoutingResult` gains a final defaulted exact `frozenset[str]` named `provisionable_child_identifiers` so current
positional constructors remain compatible. It must be a bounded subset of `blocked_identifiers`, contain only
canonical child IDs, and be empty for disabled/global-failure results. Successful routing nominates only non-retained
child projections whose stored status is `pending_fresh_base_equality`.

`filter_routing_candidates` preserves unmanaged candidates and nominated children in tracker order. Every other
managed identifier remains excluded. A malformed result normalizes atomically to the existing global failure.

`aidt_routing.dispatch` owns a frozen `AidtRouteDispatchContract` and strict loader. The loader re-reads the exact
regular card, validates source/schema/role/status/state/coordinator/service/ref/SHA/digest/fingerprints/recheck list,
and matches checkout/kind/branch against the current closed catalog. It performs no network or Git mutation. `None`
means truly unmanaged only; a malformed or ineligible managed card raises a sanitized failure.

The worker uses the tick nomination only to enter the specialized barrier. It reloads the contract before create or
resume, and delegate-aware `before_run` reloads it again immediately before backend construction. Card/config drift
therefore never falls through to a generic workspace.

## Default-off profile

`aidt_worktree` accepts exactly `{enabled: bool}`. Missing or false releases no children but a manifest guard remains
able to recognize and preserve previously owned paths. Enabled mode requires:

- enabled `aidt_routing`, file tracker, and an absolute contained workspace root;
- `workspace.reuse_policy: preserve`;
- no `after_create`, `before_run`, `after_run`, `before_remove`, or `after_done` hook;
- `agent.auto_commit_on_done: false` and `agent.auto_merge_on_done: false`.

Invalid enabled profiles fail dispatch validation. Disabled mode preserves current generic behavior for every path
without an exact AIDT manifest.

## Manifest

Store canonical JSON beneath `<workspace-root>/.symphony-aidt-worktrees/manifests/<child>.json`; locks and temporary
files stay in sibling metadata directories. Reject symlinks, non-regular files, unknown keys/schema/state, invalid
types/IDs/paths/SHA/digests, path escape, and workflow/board/catalog disagreement.

`aidt-worktree-v1` binds:

- `state`: `prepared`, `ready`, or `removed`;
- ticket/coordinator/service/kind and workflow/board identities;
- catalog checkout name, canonical service path, common-Git identity, and route repository-binding digest;
- exact branch, fixed `refs/remotes/origin/aidt-prd`, frozen base SHA, and contained worktree path;
- route scope: route/coordinator fingerprints plus source/catalog revisions;
- pre-root/registry/protected-occupancy proof, post-ready proof, and removal proof.

No raw command output, environment, URL, Jira text, credential, repository content, or arbitrary path supplied by a
card is persisted or logged.

## Git protocol and lifecycle

Use a dedicated injected binary runner with fixed argv, sanitized environment, stdin disabled, timeouts, channel
caps, process kill/reap, and strict command-specific parsers. Never use a shell. Never log captured output.

New create, under metadata and common-Git locks:

1. re-attest card/catalog; require absent manifest/path/branch/remote branch/registration;
2. snapshot root HEAD/symbolic ref/NUL status, worktree registry, protected occupancy, and identities;
3. fetch only `refs/heads/aidt-prd:refs/remotes/origin/aidt-prd` from origin, without prune/tags/submodules;
4. require the full fetched commit equals the routed checkout revision and the observed binding digest remains exact;
5. re-attest card and all preconditions; atomically write `prepared`;
6. run `git worktree add --no-track -b <branch> <path> <base-sha>` without force;
7. prove one exact registration/ref delta, exact branch/HEAD/base/no-upstream/clean new worktree, unchanged dirty root,
   and unchanged protected occupancy; write `ready`.

Backend/other uses `{feat|fix}/A20-N`; frontend uses `csk-{feat|fix}/A20-N`. The route-derived prefix must match kind
and issue type. Protected, release/merge, suffix, case, or noncanonical names are rejected.

Ready resume performs no fetch/add/reset/rebase/checkout/cleanup. It requires exact manifest scope and registration,
branch/path/service/common-Git identity, no upstream, and manifest base as an ancestor of current ticket HEAD. Ticket
worktree dirt is allowed. Root state and protected occupancy must remain unchanged.

Prepared recovery accepts only two unambiguous shapes: all target branch/path/registration artifacts absent, or the
one complete exact worktree created from the intent. Branch-only, path-only, conflicting registration, changed root,
or any mixed shape blocks and preserves evidence.

Cleanup requires an exact ready manifest plus explicit matching completed identifier, exact registration/branch/
scope, clean ticket worktree, unchanged protected occupancy, and no active lease. It runs plain `git worktree remove
<exact-path>`, verifies only that registration disappeared, and writes `removed`. It never uses force, prune, raw
recursive deletion, reset, or branch deletion.

## Workspace and orchestrator seam

Define a small delegate protocol used by `WorkspaceManager.path_for`, `create_or_reuse`, `before_run`, and `remove`.
The delegate returns `None` only when it does not own the identifier/path. Once an exact AIDT card or manifest is
recognized, success or sanitized failure is final; generic code cannot run. `remove` accepts optional completed-ticket
authorization. Unauthorised AIDT removal is a handled preservation, not a decline.

All manager construction/reload sites receive the runtime delegate. Core candidate filtering passes both complete
managed and provisionable sets. Core Done cleanup supplies the matching identifier; non-Done/startup/reconcile paths
without authorization preserve AIDT worktrees. Enabled profile validation prevents direct generic commit/merge/hook
paths. Initial and retry dispatch both enter `_run_agent_attempt`, so no second worker path is needed.

Health exposes only enabled/status/create/resume/failure counts, last-success time, bounded category/ref, and
consecutive failures. Reload disables new nomination but retains manifest ownership. A config or workflow reload error
keeps the current global routing barrier closed.

## Test boundary

- `test_aidt_route_dispatch_contract.py`: DTO/result/subset/filter/card/config drift and facade import permutations.
- `test_aidt_worktree_contract.py`: config/profile/branch/scope/manifest/error totality and bounds.
- `test_aidt_worktree_manifest.py`: atomic transitions, collisions, symlinks, path containment, concurrent CAS.
- `test_aidt_worktree_git_state.py`: capped runner and strict NUL/ref/registry/root/protected parsing.
- `test_aidt_worktree_provisioner.py`: local bare remote create/resume/recovery/collision/concurrency/dirty root/cleanup.
- `test_aidt_worktree_runtime.py`: delegate ownership, no fallback, reload, health, final re-attestation.
- targeted orchestrator/workspace tests: initial/retry pre-backend barrier, Done authorization, non-Done preservation,
  default-off and unmanaged parity.

No test accesses Jira, the network, or a live AIDT checkout. A later activation frontier owns real fetch and E2E.

## Delegation record

The route/dispatch contract was produced by a fresh-context explorer. Two separate fresh-context architecture agents
were attempted; both remained in read-only execution without producing the requested file and were interrupted. This
report consolidates their bounded brief with the landed-code inspection and the completed explorer artifact; a new
fresh-context plan attacker must independently challenge every binding before Build.
