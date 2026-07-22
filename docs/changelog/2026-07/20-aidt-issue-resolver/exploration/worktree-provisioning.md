# Frontier 003 — AIDT worktree provisioning exploration

> Superseded detail: references below to Frontier 002 recording checkout `HEAD` are replaced by the binding
> immutable-object decision in `routing-git-object-trust.md` and Frontier 002 PLAN. Frontier 003 compares its fresh
> fetched `origin/aidt-prd` SHA to the route's fixed-ref `checkout_revision`.

Date: 2026-07-20
Mode: read-only code and Git inspection. No network, fetch, branch, worktree, checkout, cleanup, product/test edit,
or secret inspection was performed.

## Decision

Frontier 003 needs a dedicated, default-off Python provisioner behind Symphony's existing `WorkspaceManager`.
It must not adapt `scripts/symphony-setup-worktree.sh` or the current shell hooks. The generic script is designed for
the Symphony host repository: it derives a base from the host's current branch, force-removes a prior registration,
runs global `git worktree prune`, reuses any same-name branch, links the host board, and installs a Symphony virtual
environment. Those behaviors are incompatible with a crowded, user-owned AIDT service repository.

The AIDT path should still reuse the stable outer lifecycle:

1. `WorkspaceManager.path_for(identifier)` supplies a contained deterministic ticket path.
2. `Orchestrator._dispatch` records that path in the run lease before a worker starts.
3. `WorkspaceManager.create_or_reuse` is the one preparation seam before `before_run` and backend construction.
4. Existing before/after-run and terminal cleanup call sites remain the lifecycle boundary, but AIDT provisioned
   workspaces must use dedicated policies rather than the generic hooks, auto-commit, auto-merge, or raw `rmtree`.

The provisioner must write an atomic intent manifest before `git worktree add`, freeze the fetched
`origin/aidt-prd` commit, and treat exact create/resume as a state machine. It may complete an untouched prepared
intent or finalize an already exact worktree after interruption. Any other mismatch blocks without reset, rebase,
branch reuse, recreation, force removal, prune, or branch deletion.

## Theory and real-world mapping

A routed Symphony child represents one independently owned repository change. The AIDT feature branch represents
that change's durable Git history; the linked worktree is only its isolated editing location. The provisioner is
therefore an identity binder, not a checkout convenience:

```text
route-owned child + current catalog
             |
             v
service / route revision / scope fingerprint
             |
             v
fetched origin/aidt-prd SHA -> branch -> exact worktree registration
             |
             v
atomic local manifest used for restart-safe resume and scoped cleanup
```

The main service checkout is user state. It may already be dirty or occupy a protected branch. Provisioning may
write the service repository's shared Git metadata to add one feature ref and one worktree registration; it must not
change the main checkout's `HEAD`, index, tracked/untracked files, branch occupancy, or existing registrations.

## Existing Symphony seams

| Seam | Current behavior | Frontier 003 use or gap |
|---|---|---|
| `workspace.py:99-184` `WorkspaceManager` | Sanitizes an issue identifier, contains it under one workspace root, creates/reuses the directory, runs `after_create`, and records `created_now`. | Keep path containment and the public lifecycle. Add an optional AIDT provisioner delegate; do not encode service Git policy in the generic manager. |
| `workspace.py:154-183` `create_or_reuse` | A missing path is created before the shell hook. A new-workspace hook failure recursively deletes the path. | Generic cleanup can orphan a partially created AIDT branch/registration. The AIDT delegate must own its intent, command, verification, and failure preservation end to end. |
| `workspace.py:263-321` owner marker | Stores only `workflow_dir`, `board_root`, and enclosing Symphony `repo_root`. Missing/corrupt/legacy markers are accepted. | Useful generic collision guard, but not an AIDT resume authority. A strict service/branch/base/path/scope manifest is required and a missing manifest must block adoption. |
| `workspace.py:235-252` `remove` | Ignores `before_remove` failure and then recursively removes the directory. | Unsafe for AIDT. A registered AIDT path must never fall through to raw `rmtree`; cleanup needs manifest, terminal authorization, clean-worktree, and exact-registration proof. |
| `workspace.py:520-691` `commit_workspace_on_done` | Best-effort stages files and may `git reset --soft` to `symphony.basesha` before committing. Failures only warn. | Must be disabled for the AIDT profile. It can rewrite reviewed service history and its lenient failure contract is not delivery evidence. |
| `orchestrator/core.py:3462-3504` dispatch | Resolves `path_for`, acquires a durable run lease, and creates `RunningEntry` before scheduling the worker. | Reuse. The deterministic AIDT path must be the same path recorded in the lease and manifest. |
| `orchestrator/core.py:3620-3668` worker start | Calls `create_or_reuse`, binds the returned path, runs `before_run`, then constructs the backend with `cwd=workspace.path`. | Exact integration point. Provisioning failure must remain fail-closed before backend construction. |
| `orchestrator/core.py:1829-1871`, `5195-5290`, `5985-6120`, `6188-6270` | Done/reconcile/startup paths auto-commit and remove workspaces; non-Done terminal states can also be reaped. | AIDT manifests need cleanup authorization. Blocked/Human Review/interrupted worktrees must be preserved, and generic cleanup must not process them. |
| `orchestrator/helpers.py:53-60` hook env | Exposes only global feature-base and merge-target values from workflow config. | Cannot safely carry per-ticket service/path/base data, and mutable global hook env would race multi-service dispatch. Use typed per-ticket data instead. |
| `run_registry.py` plus dispatch state | Prevents duplicate live attempts and persists the expected workspace path. | Reuse as a dispatch lease, but do not mistake it for Git ownership; the AIDT manifest remains authoritative after process interruption. |
| `trackers/file.py` raw frontmatter parsing | Unknown frontmatter is preserved while `Issue` exposes only generic dispatch fields. | Keep `Issue` narrow. Re-read and validate the route-managed child at provision time rather than trusting body text or widening every tracker model. |

### Existing worktree implementation is not reusable as policy

`scripts/symphony-setup-worktree.sh:94-109` takes a shared Git lock, then runs force remove, global prune, and branch
reuse before creating `symphony/<ID>`. `WORKFLOW.md` currently sets `workspace.reuse_policy: refresh`, invokes that
script, fetches with `|| true` before turns, commits/amends after every turn, force-removes before cleanup, leaves
`auto_commit_on_done` at its true default, and explicitly enables generic `auto_merge_on_done`.

An AIDT profile must reject or replace all of those settings:

- `workspace.reuse_policy` is `preserve` for AIDT; resume validation decides reuse.
- generic `after_create`, Git-fetching `before_run`, commit/amend `after_run`, and force-remove `before_remove` are
  absent for routed service worktrees;
- `agent.auto_commit_on_done: false` and `agent.auto_merge_on_done: false` are activation requirements;
- no provisioning command uses a shell or interpolated card text.

The temporary detached worktree in `continuous_improvement.py:390-495` is a useful injectable-runner test pattern,
but its unconditional `worktree remove --force` is not a cleanup policy for user-owned AIDT work.

## Trusted inputs

Only the following inputs can authorize provisioning:

1. Last-good, enabled `aidt_routing` configuration with a validated absolute AIDT root and closed service catalog.
2. A route-managed child card with identifier `<A20-key>--<service-id>`, exact source kind
   `aidt-route-child`, matching coordinator/service keys, ready route status, and a valid route marker.
3. Route-owned structured fields: source revision, catalog revision, route fingerprint, canonical service ID,
   relative catalog checkout, explicit backend/frontend kind, checkout revision, and derived branch prefix.
4. The current catalog entry for the same service. It supplies the checkout path and kind again; the card never
   supplies an arbitrary path. Card/catalog disagreement blocks.
5. A freshly fetched `refs/remotes/origin/aidt-prd^{commit}` resolved by fixed Git argv in that canonical service
   repository. The resulting lowercase 40-hex SHA is the only new-branch start point.
6. A manifest already owned by the same workflow/board/ticket when resuming.

Never trust the Jira/body marker, local `Issue.branch_name`, title, labels, a transient `worktrees/**` path, current
service branch, `FETCH_HEAD`, abbreviated SHA, remote default branch, or an existing same-name branch.

### Route revision versus source base

Frontier 002's frozen contract records the canonical checkout's `HEAD` revision. Frontier 003 must reverify that
stored commit before mutation, as required by the Frontier 002 plan attack, but it must separately fetch and freeze
`origin/aidt-prd` as the worktree base.

These commits cannot silently differ: ownership/code evidence observed at one revision is not proof for a worker
starting at another revision. The minimal fail-closed rule is:

- new provisioning requires `routing.checkout_revision == fetched origin/aidt-prd SHA`;
- mismatch produces a bounded `route_base_mismatch` and requests rerouting/recheck;
- the provisioner never substitutes checkout `HEAD`, `aidt-dev`, or `aidt-stg` as the base.

A later routing improvement can inspect anchors at the fetched base commit rather than requiring the main checkout
to sit on `aidt-prd`. That is preferable for broad activation, but silently relaxing the equality in Frontier 003
is not safe.

## Manifest contract

Store one canonical JSON file outside the product worktree, under the configured Symphony workspace metadata root,
for example `.symphony-aidt-worktrees/<child-id>.json`. Write by same-directory temporary file plus atomic replace;
reject symlinks, non-regular files, unknown keys/versions, duplicate/case-colliding IDs, and invalid scalar types.

Minimum `aidt-worktree-v1` fields:

- `state`: `prepared`, `ready`, or `removed`;
- `ticket_id`, `coordinator`, `service_id`, and `service_kind`;
- `workflow_identity` and `board_identity`;
- canonical checkout relative name, resolved checkout path, top-level/common-Git identity, and route checkout SHA;
- exact `branch`, fixed `base_ref`, frozen `base_sha`, and resolved contained `worktree_path`;
- `scope`: route fingerprint, source revision, catalog revision, and the child's service-specific route slice;
- pre-provision registry/protected-occupancy/status digests and creation timestamp;
- postcondition digest when `ready`, and removal evidence when `removed`.

The `scope` object is not a guessed file allowlist: Plan/Build have not selected files yet. It binds the workspace to
the exact route-owned service slice so a changed source/catalog/route cannot inherit an old worktree silently.
Future plan evidence may reference this fingerprint; it must not rewrite the provisioning identity.

Do not put raw Git output, remote URLs, environment values, Jira prose, credentials, or file content in the manifest
or logs.

## Branch and service rules

- Resolve the service only from the current catalog and use fixed Git argv rooted at the canonical service; never
  construct a shell command from card data.
- Verify `--show-toplevel`, object format, common Git directory identity, and the stored route commit before fetch
  and again before `worktree add`.
- Bug maps to `fix`; every other reviewed issue type maps to `feat`.
- Backend/other branch: `{feat|fix}/A20-N`; frontend branch: `csk-{feat|fix}/A20-N`. Use catalog `kind`, not a
  `-web` suffix, because `admin` is frontend too.
- Explicitly reject `aidt-dev`, `aidt-stg`, `aidt-prd`, merge/release branch families, malformed Jira keys, extra
  suffixes, and a route prefix inconsistent with kind/type.
- Block if either the local feature ref or its remote-tracking ref already exists without the exact ready manifest.
- Create with the frozen SHA and `--no-track`; verify the branch has no upstream.
- A service's pre-existing protected occupancy is allowed but immutable. The invariant is equality of the protected
  occupancy set before and after, not an empty set.
- The only allowed creation delta is one exact feature ref, one exact worktree registration, and the expected shared
  Git administrative metadata. No root checkout branch/index/status delta is allowed.

## Deterministic lifecycle

### New create

1. Re-read the child and catalog; validate source/route/catalog fingerprints and exact service scope.
2. Resolve the deterministic contained worktree path. Require absent manifest, absent target, absent local and
   remote-tracking feature refs, and no existing registration for path or branch.
3. Snapshot the canonical checkout's `HEAD`, symbolic branch, NUL-safe status, full porcelain worktree registry,
   and protected occupancy. Dirty/untracked root state is allowed and must remain byte-for-byte unchanged.
4. Fetch only `aidt-prd` from `origin` with fixed argv, bounded timeout/output, sanitized environment, and no output
   logging. Resolve the full remote-tracking commit.
5. Re-read the card, repository identity, route commit, root snapshot, target, refs, and registry. Any drift blocks
   before the branch/worktree mutation.
6. Atomically write the `prepared` intent with the frozen base SHA.
7. Under a per-common-Git-dir provision lock, run exactly one `git worktree add --no-track -b <branch> <path>
   <base_sha>`.
8. Verify exact registration, branch, `HEAD == base_sha`, clean new worktree, no upstream, unchanged root status,
   unchanged protected occupancy, and an exact one-record registry delta.
9. Atomically mark the manifest `ready`; only then may the backend start.

The lock must serialize this managed process without stealing or deleting another process's lock. Git's own lock
failure or any external race is a visible collision, not permission to prune or retry destructively.

### Resume

Resume never fetches a new base or runs `worktree add` for a `ready` manifest. It requires exact equality for ticket,
coordinator, service, kind, checkout identity, route checkout SHA, branch, base SHA/ref, worktree path, and scope.
It then verifies:

- the branch is registered at only that path and that path points back to the same common Git directory;
- the worktree is on the exact branch, has no upstream, and stored `base_sha` is an ancestor of current `HEAD`;
- no protected branch gained occupancy;
- dirty/uncommitted ticket work is allowed and preserved.

Route/source/catalog drift, branch history that no longer descends from the base, a moved path, missing manifest,
duplicate registration, or foreign ownership blocks. There is no automatic reset, rebase, recreate, or cleanup.

### Interrupted create recovery

| Observed `prepared` state | Safe action |
|---|---|
| No branch, target, or registration exists and all recorded preconditions still match | Re-run the single original add using the stored base, then verify/finalize. |
| Exact branch/path/registration exists, `HEAD == base_sha`, clean tree, no upstream, and all invariants match | Treat as add-completed/manifest-update-interrupted; mark `ready` without recreating. |
| Branch exists without exact path/registration, path exists without exact registration, registration is missing/prunable/foreign, target is non-empty, or any identity/scope differs | Block for manual recovery; preserve every artifact. |
| Manifest is corrupt, missing after an apparent worktree exists, or state is `removed` | Block; never adopt or recreate automatically. |

This ordering gives recoverability before and after the atomic Git command while refusing ambiguous partial Git
state.

## Collision and idempotency matrix

| Condition | Result |
|---|---|
| Same child, exact `ready` manifest and exact Git binding | Idempotent resume; no mutating Git command. |
| Same child, route/source/catalog fingerprint changed | Block `scope_mismatch`; retain worktree and manifest. |
| Existing branch or remote-tracking branch without owned manifest | Block `branch_collision`. |
| Branch occupied by another worktree, even if its name/base looks plausible | Block `branch_occupied`. |
| Target path is file, symlink, non-empty directory, outside root, or registered to another repository | Block `path_collision`. |
| Stored base object missing or not an ancestor of active branch | Block `base_mismatch`; no fetch/rebase/reset during resume. |
| Protected occupancy differs post-command | Block and preserve evidence; do not try destructive rollback. |
| Root dirty snapshot changes during provisioning | Block concurrent mutation; do not touch user files. |
| Two simultaneous creates for the same child | Manifest/lock winner proceeds; loser re-reads and resumes or blocks. |
| Two different children in the same service | Serialize shared Git metadata, then allow one exact delta per manifest. |

All errors exposed to health/logs should be allowlisted categories plus canonical child/service IDs. Do not include
absolute paths, raw Git stderr/stdout, source text, or exception strings.

## Cleanup safety boundary

Frontier 003 tests should specify cleanup, but this exploration does not perform or implement any deletion.

An eventual cleanup call must receive explicit completed-ticket authorization, not infer it from path existence.
Before removal it must re-read the manifest/card and require:

- exact `ready` manifest ownership and route scope;
- terminal completion state authorized by the later delivery gate; Blocked, Human Review, interrupted, failed QA,
  and unknown states are preserve-only;
- exact branch/path/common-Git registration, no protected branch occupancy delta, and a clean worktree;
- no nested/untracked/uncommitted content that plain `git worktree remove <exact-path>` would discard.

Use plain scoped `git worktree remove <exact-path>` from the recorded service repository. Do not use `--force`, raw
`rmtree`, `git worktree prune`, or `git branch -d/-D`. Verify that exactly the owned registration disappeared, the
feature branch still exists at the same SHA, protected occupancy is unchanged, and unrelated/prunable registrations
are byte-for-byte preserved. Then atomically mark the manifest `removed` with evidence. A missing path/registration,
dirty tree, command failure, or postcondition mismatch preserves state and blocks for manual recovery.

This requires an AIDT-aware removal delegate. The current `WorkspaceManager.remove` and terminal reconcile behavior
must never fall through on an AIDT manifest.

## Read-only AIDT observations

Observed locally on 2026-07-20; these are evidence, not durable configuration:

- `aidt-viewer-api` main checkout is on `aidt-prd`; `HEAD` and local `origin/aidt-prd` both resolve to
  `84a3d1723f2ba35150fb56d400621d4f8cc261fb`. It has untracked `.cbmignore`, `docs/changelog/2026-07/`, and
  `worktrees/`, plus 20 registered worktrees. No registered worktree occupies local `aidt-dev` or `aidt-stg`.
- Local and remote-tracking `feat/A20-1188` refs are absent in `aidt-viewer-api`, so the sample has no current branch
  collision. The routed sample's backend convention is `feat/A20-1188`.
- `aidt-lms-api` main checkout already occupies `aidt-stg`; its local `origin/aidt-prd` is
  `2b01b03dd4739380cdc5f8ea28a7966b86c1cebb`, and its registry contains many unrelated and prunable worktrees.
  Global prune would mutate user-owned state.
- `aidt-viewer-web` main checkout occupies `aidt-dev` and is behind its tracking branch; its local
  `origin/aidt-prd` is `9a4d2d5427e6d769dbbc554fe08e4b730a9b18ab`.
- AIDT root policy explicitly forbids newly occupying local `aidt-dev`/`aidt-stg` in `lms-api` and `viewer-api`,
  requires base `origin/aidt-prd`, backend `{feat|fix}/A20-*`, frontend `csk-{feat|fix}/A20-*`, and final porcelain
  worktree checks. Pre-existing protected occupancy therefore has to be snapshotted and preserved, not assumed away.

There is an upstream activation conflict: Frontier 002's plan currently rejects dirty/untracked canonical checkouts,
while the sampled `viewer-api` checkout is dirty and Frontier 003 is required to preserve dirty user state. Frontier
003 must not clean it or weaken routing silently. Either routing is amended to observe the trusted base commit without
requiring a clean main worktree, or live routing remains blocked until an operator resolves that gate.

## Test seams and required fixture proof

Create `tests/test_aidt_worktree_provisioner.py` with temporary local repositories and local bare remotes only. Inject
the Git runner, clock, identity probe, lock, manifest-write fault, and post-add fault; do not use network or the real
AIDT tree.

Required cases:

1. backend create from fetched SHA: exact branch/path/registration, `HEAD == base`, no upstream, ready manifest;
2. frontend and `admin` prefixes plus Bug/non-Bug derivation; protected names rejected;
3. dirty canonical root with tracked, staged, and untracked sentinels remains exactly unchanged;
4. pre-existing `aidt-dev`/`aidt-stg` occupancy remains identical while one feature worktree is added;
5. exact ready resume is mutation-free and preserves dirty ticket work;
6. every service/branch/base/path/scope/checkout-identity mismatch blocks with zero mutation;
7. local ref, remote-tracking ref, branch occupancy, path, symlink, case, foreign repo, and duplicate-registration
   collisions;
8. origin movement after fetch still creates from the frozen SHA, while route/base revision mismatch blocks;
9. interruption before add safely completes; interruption after exact add safely finalizes; every ambiguous partial
   shape blocks without cleanup;
10. concurrent same-ticket and same-service creates serialize and remain idempotent;
11. Git timeout/failure output and source/path text do not leak into result, health, or logs;
12. cleanup contract: only exact completed+clean owned worktree is removed, branch remains, unrelated/prunable and
    protected registrations remain; dirty/blocked/missing/foreign cases are preserve-only;
13. orchestrator integration proves the backend never starts on provisioning failure and starts in the AIDT service
    worktree on success;
14. generic non-AIDT workspace tests remain unchanged.

Reuse the style, not the unsafe policy, of existing tests:

- `tests/test_workspace.py`: path containment, create/reuse collision, hook failure, branch env, real Git worktree,
  and concurrent shared-Git metadata fixtures;
- `tests/test_aidt_routing.py`: temporary service/catalog/card fixtures and injected bounded Git runner;
- `tests/test_orchestrator_dispatch.py` and `tests/test_agent_lifecycle_e2e.py`: fake workspace manager and proof that
  preparation precedes backend start;
- `tests/test_continuous_improvement.py`: injected worktree command runner and real local-worktree E2E pattern;
- `git worktree list --porcelain` snapshots before/after every real Git fixture, with exact parsed set assertions.

The ticket's focused command remains:

```bash
pytest -q tests/test_aidt_worktree_provisioner.py
```

Affected regression commands should also include `tests/test_workspace.py`, `tests/test_aidt_routing.py`,
`tests/test_orchestrator_dispatch.py`, `tests/test_orchestrator_reconcile.py`, and
`tests/test_agent_lifecycle_e2e.py`.

## Minimal proposed Frontier 003 file boundary

1. New `src/symphony/aidt_worktree.py` — closed manifest/request models, route-child revalidation, canonical
   service/base reader, fixed-argv Git runner, registry parser, create/resume state machine, collision categories,
   and scoped cleanup contract.
2. `src/symphony/workspace.py` — optional provision/remove delegate protocol. Preserve generic behavior byte-for-byte
   when the delegate declines an identifier; prevent generic hook/rmtree fallthrough for an owned AIDT manifest.
3. `src/symphony/orchestrator/core.py` — construct/hot-reload the default-off provisioner, inject it into the manager,
   keep provisioning before backend construction, pass terminal cleanup authorization, and expose bounded health.
4. `src/symphony/aidt_routing.py` — only the smallest integration needed to expose/revalidate a trusted ready child
   and release that exact child from Frontier 002's temporary dispatch barrier. Coordinators, review/stale/retained
   children, and all routed IDs on global failure remain blocked.
5. New `tests/test_aidt_worktree_provisioner.py` — unit, real temporary Git, fault-injection, concurrency, cleanup,
   and orchestrator integration proof.

Do not change `Issue`, Jira intake, generic shell setup, auto-merge, promotion, Jenkins, stage prompts, UI/TUI, or AIDT
product repositories in this frontier. Frontier 004 owns delivery-stage authorization; Frontier 006 owns the separate
temporary `origin/aidt-dev` merge worktree. Provisioning must not absorb either concern.

## Risks and gates to freeze in the Frontier 003 plan

1. **Route/base revision mismatch:** block unless routing evidence and fetched base name the same commit; do not guess.
2. **Dirty-routing conflict:** current Frontier 002 clean-checkout gate conflicts with the live dirty `viewer-api`;
   resolve upstream or retain a visible live blocker.
3. **Generic lifecycle fallthrough:** current refresh hooks, auto-commit, auto-merge, and removal can rewrite or delete
   AIDT work. AIDT activation must validate that they are disabled/bypassed.
4. **Crash ambiguity:** intent must precede Git mutation; only two exact prepared-state recoveries are automatic.
5. **Concurrent Git metadata:** serialize by common Git directory, recheck after the lock, and fail closed on external
   races without stealing locks or pruning.
6. **Blocked cleanup:** current generic reconcile can reap non-Done terminals. Owned AIDT manifests require explicit
   preserve/cleanup semantics before dispatch is enabled.
7. **Secrets in Git errors:** bound and suppress raw fetch output; emit categories only.
8. **Branch collision after completed work:** branches are deliberately retained. A repeated Jira key therefore
   blocks for operator disposition rather than deleting/reusing history.

No implementation should begin until the Frontier 002 route-child schema is present and the plan freezes the exact
fields Frontier 003 consumes.
