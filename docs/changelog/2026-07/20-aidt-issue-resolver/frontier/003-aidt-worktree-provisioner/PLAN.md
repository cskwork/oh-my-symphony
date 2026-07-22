# PLAN - Frontier 003 AIDT Worktree Provisioner

## Approval

- Status: auto-approved under the user's accepted infrastructure plan.
- Route: GREENFIELD / Wayfinder ticket 003.
- Max Build/Verify iterations: 3.
- Live mutation: forbidden; tests use temporary local Git repositories and remotes only.

## Theory

A routed child is a trusted request to prepare one service, not permission to run a backend in the generic Symphony
directory. The real-world identity is the tuple of ticket, service repository, route scope, fetched production-base
commit, feature branch, and linked worktree. Frontier 003 must bind that tuple durably and re-prove it immediately
before Codex starts. The main service checkout is user state: dirty files and existing worktrees are observations to
preserve, never inputs to normalize.

## Frozen Contract

### 1. Route nomination and attestation

1. Append a defaulted exact `frozenset[str]` named `provisionable_child_identifiers` to `AidtRoutingResult` so all
   existing positional constructors remain source-compatible.
2. A valid provisionable set is bounded by `MAX_CHILDREN`, contains only canonical child IDs, is a subset of the
   complete `blocked_identifiers`, and is empty when routing is disabled, dispatch is globally denied, or status is
   failure. Invalid combinations atomically normalize through the existing canonical routing failure.
3. Successful routing nominates only `RouteResolution.children` with exact schema/role/status
   `aidt-route-object-v2`/`child`/`pending_fresh_base_equality`; retained/stale/review/coordinator projections remain
   blocked.
4. `filter_routing_candidates` gains the provisionable set. It preserves tracker order, retains unmanaged issues and
   nominated children, and excludes every other managed ID. Default empty provisionable input preserves landed
   behavior.
5. Add frozen `AidtRouteDispatchContract` and `load_route_dispatch_contract`. The loader validates the exact regular
   child card/path, configured ready state, source tuple, route schema/role/status, coordinator/service identity,
   fixed checkout ref, full SHA/digests/fingerprints, confidence, exact recheck list, current catalog checkout/kind,
   and route-derived branch. It does no Git mutation. `None` is valid only for a truly unmanaged identifier; a
   malformed or ineligible managed card raises a sanitized failure.

### 2. Default-off configuration

1. `aidt_worktree` is missing/false by default and accepts exactly `{enabled: bool}`.
2. Enabled mode requires enabled AIDT routing, file tracker/board, absolute workspace root, reuse `preserve`, every
   generic hook absent, and generic auto-commit/auto-merge false. Validation occurs before route children can be
   nominated or a workspace can be created.
3. Disabled mode nominates no AIDT child. A manifest guard remains attached so configuration disable/restart cannot
   make an already owned AIDT path fall through to generic removal.
4. Existing unmanaged workspace, tracker, routing-disabled, and import behavior is byte-for-byte compatible.

### 3. Branch and path identity

1. The only base ref is `refs/remotes/origin/aidt-prd`; the only base commit is a lowercase full SHA-1 fetched for
   the routed service and equal to the recorded `checkout_revision`.
2. Bug maps to `fix`; every other accepted issue type maps to `feat`. Backend/other branch is
   `{feat|fix}/A20-N`; frontend branch is `csk-{feat|fix}/A20-N`. The stored route prefix must equal the derived value.
3. Reject aidt-dev/aidt-stg/aidt-prd, release/merge families, suffixes, alternate case, traversal/control text, and
   existing local or remote-tracking feature refs without one exact ready manifest.
4. The worktree path is the generic contained `WorkspaceManager.path_for(child-id)` path. Card text never supplies a
   filesystem path. Canonical service paths come only from the closed catalog under `aidt_root`.

### 4. Manifest state machine

1. Store strict canonical `aidt-worktree-v1` JSON at
   `<workspace-root>/.symphony-aidt-worktrees/manifests/<child-id>.json`; locks/temp files use sibling metadata dirs.
2. Reject symlinks, non-regular files, unknown/missing keys, non-exact scalar/container types, noncanonical values,
   path escape, identity drift, duplicate/case collision, and oversized files before use.
3. Bind state, ticket/coordinator/service/kind, workflow/board identities, catalog checkout/canonical service/common
   Git identity, route binding digest, branch/base ref/base SHA/path, exact route scope, pre-proof, post-proof, and
   removal proof. Route scope is fingerprints plus source/catalog revisions, not an implementation file allowlist.
4. Writes use same-directory exclusive temp creation, flush/fsync, compare-and-swap expectation, atomic replace, and
   parent-directory fsync where supported. State transitions are only absent -> prepared -> ready -> removed.
5. Persist/log no raw Git output, remote URL, environment, Jira prose, credential, repository content, or unbounded
   path/error text.

### 5. Bounded Git protocol

1. Use an injected binary runner with fixed argv, no shell/stdin/prompt/global config/lazy fetch, bounded timeout and
   stdout/stderr, overflow kill/reap, and strict command-specific parsers. Error surfaces expose allowlisted category
   plus canonical card/service ref only.
2. Snapshot canonical top-level/common-Git identity, object format, root HEAD/symbolic ref/NUL status, worktree
   registry, target refs/upstream, and protected occupancy. Dirty/untracked root state is allowed and must compare
   byte-for-byte after every operation.
3. Fetch only `aidt-prd` from origin with an exact refspec, no prune/tags/submodules/write-FETCH_HEAD. Fetch may move
   only that remote-tracking ref; no user checkout/index/worktree is changed.
4. Creation is exactly `git worktree add --no-track -b <branch> <path> <base-sha>` without force. Verify one expected
   branch/registration delta, clean new worktree, exact branch/HEAD/base, no upstream, unchanged root, and unchanged
   protected occupancy.
5. No code path invokes reset, rebase, checkout/switch, prune, recursive removal, force removal, or branch deletion.

### 6. Create, resume, interruption, and concurrency

1. New create re-attests card/catalog, proves absent manifest/path/ref/registration, locks manifest and common Git,
   snapshots, fetches, proves route/base/binding equality, rechecks drift, writes prepared, adds the worktree, verifies,
   writes ready, and returns `created_now=True`.
2. Exact ready resume performs no fetch or mutation. It requires exact card/manifest scope, service/common-Git/path/
   branch/registration identity, no upstream, base ancestor of current ticket HEAD, unchanged root/protected
   occupancy, then returns `created_now=False`. Dirty ticket work is allowed.
3. Prepared recovery accepts only all target branch/path/registration artifacts absent, or the one complete exact
   worktree described by the intent. The former continues add; the latter verifies/finalizes. Every mixed/ambiguous
   shape blocks and preserves evidence.
4. Per-manifest plus per-common-Git locks serialize duplicate and cross-ticket creation in the same service. All
   assumptions are re-read after lock acquisition and immediately before mutation; external drift blocks.
5. Failure after prepared/add preserves intent/worktree. No exception path removes a branch, path, or registration.

### 7. Delegate, dispatch, cleanup, and health

1. Add a small workspace delegate protocol. `path_for`, `create_or_reuse`, `before_run`, and `remove` ask it first.
   `None` means unmanaged only. Once a canonical child or manifest is recognized, success/failure/preservation is
   final and generic mkdir/hooks/rmtree cannot run.
2. `create_or_reuse` reloads the tick-nominated contract and provisions/resumes before backend construction.
   Delegate-aware `before_run` re-reads the card/config/manifest and final Git identity immediately before backend;
   it skips generic hooks. Both initial and retry workers use this same `_run_agent_attempt` path.
3. `remove` accepts optional `completed_identifier`. AIDT cleanup requires matching explicit completion, exact ready
   manifest, no active run lease, clean worktree, exact registration/branch/scope, and unchanged protected occupancy.
   It runs plain `git worktree remove <path>`, verifies the exact registration disappeared, and writes removed.
4. Unauthorised, non-Done, inactive, blocked, startup, or mismatch cleanup is a handled preservation. It never
   declines to generic cleanup. Cleanup never deletes the feature branch.
5. Core Done cleanup passes the matching identifier. Enabled profile rules ensure core never calls generic
   commit/merge/hooks for AIDT worktrees. Unmanaged Done behavior is unchanged.
6. Health exposes only enabled/status/create/resume/failure counts, bounded category/ref, last success, and
   consecutive failures. Reload stops new nomination on error/disable while existing manifests remain owned.

## Cohesive File Scope

Product:

- `src/symphony/aidt_routing/{__init__,contract,dispatch,runtime}.py`
- `src/symphony/aidt_worktree/{__init__,contract,manifest,git_state,provisioner,runtime}.py`
- `src/symphony/workspace.py`
- `src/symphony/orchestrator/core.py`
- minimal workflow validation export only if the existing raw-config seam cannot enforce the closed profile.

Tests:

- `tests/test_aidt_route_dispatch_contract.py`
- `tests/test_aidt_worktree_contract.py`
- `tests/test_aidt_worktree_manifest.py`
- `tests/test_aidt_worktree_git_state.py`
- `tests/test_aidt_worktree_provisioner.py`
- `tests/test_aidt_worktree_runtime.py`
- surgical edits to existing routing/workspace/orchestrator tests for compatibility and integration proof.

Run-vault and Wayfinder files may change. No AIDT repository, live profile, legacy shell script, Jenkins, Jira, merge,
push, dashboard, TUI, stage policy, prompt, or deployment file may change.

## TDD Sequence

1. Red route/result/filter/DTO tests prove current all-managed blocking and absence of typed attestation.
2. Red contract/manifest tests bind exact schemas, paths, states, CAS, profile rejection, and total bounded failures.
3. Red Git fixtures prove dirty-root/protected occupancy preservation, fixed fetch/add protocol, create/resume/recovery/
   collision/concurrency/cleanup, and forbidden command absence.
4. Red workspace/core tests prove generic fallback currently occurs or the route child cannot reach the worker; then
   prove delegate ownership, initial/retry final barrier, Done authorization, non-Done preservation, reload/health.
5. Implement the minimum cohesive layers in the same order, keeping product functions <=50 lines and nesting <=4.
6. Run isolated suites, affected matrices, full repository parity, Ruff, Pyright, AST structure, tracked/no-index
   whitespace, doctor, and fresh literal commit gate. A fresh verifier owns final evidence.

## Binding Fixture Matrix

- create success at recorded SHA; feature and bug plus backend/frontend branches;
- base mismatch after fetch; binding/card/config/root/registry drift at every recheck seam;
- dirty tracked/untracked root and pre-existing protected occupancy preserved byte-for-byte;
- local/remote ref, path, branch, worktree, manifest, symlink, case, traversal, and foreign-owner collisions;
- exact ready resume with commits/dirty ticket work and no mutation commands;
- prepared none/exact recovery plus branch-only/path-only/mixed ambiguity;
- duplicate process/thread attempt and two tickets in one common Git dir;
- cleanup missing/wrong completion, dirty worktree, active lease, mismatch, success, removed idempotence;
- default-off/unmanaged parity, unsafe profile rejection, enable/disable/reload, failure-health sanitization;
- route coordinator/review/stale/retained exclusion, child nomination, tick-to-worker and final re-attestation drift;
- initial/retry backend construction ordering and no generic hook/commit/merge/rmtree fallback.

## Rejected Alternatives

- Tick-time provisioning: performs Git I/O for tickets without slots and leaves a tick-to-worker race.
- Shell hooks or adapting `symphony-setup-worktree.sh`: interpolated state, force removal, prune, branch reuse, and host
  repository assumptions violate AIDT ownership.
- Raw board parsing inside `workspace.py`: duplicates routing trust and couples generic workspace code to AIDT.
- Generic owner marker alone: it does not bind service, branch, base, Git identity, route scope, or cleanup authority.
- Auto-cleaning partial worktrees or stale branches: destroys evidence and user work; ambiguity must block.
- Fetch/rebase on resume: changes an in-progress ticket's frozen base and violates exact restart identity.

## Binding Amendment 1 - Plan Attack Closure

This amendment supersedes every conflicting statement above. In particular, metadata is workflow-relative rather
than workspace-relative; cleanup requires a verified capability and matching active owner rather than a bare
identifier or lease absence; state includes `removing`; root proof includes dirty content; and failure disposition is
durable.

### A. Atomic route pair and change kind

1. `load_route_dispatch_contract` takes the existing coordinator and child file locks in canonical case-folded ID
   order, reads each exact regular no-follow file with a 1 MiB per-file cap, and validates both while both locks are
   held. Before/after lstat identity plus bounded byte hashes must be stable. Writers outside the lock are detected by
   the repeated identities/hashes.
2. The coordinator must own the child in `children`, not `retained_children`; its selected services, checkout refs/
   revisions/bindings, fingerprints, source/catalog revisions, and route status must agree with the child's exact
   source/routing projection. Review/stale/coordinator-only or a missing/malformed counterpart is an owned failure.
3. Canonical projection bytes are JSON of route-owned `source` plus `routing` fields only, excluding mutable card
   state/body/notes/timestamps. `route_pair_digest = SHA256("aidt-route-pair-v1\0" + coordinator_projection_digest +
   "\0" + child_projection_digest + "\0" + sorted complete child IDs)`. File byte hashes prove read stability but do
   not enter durable scope, so later non-route notes cannot steal worktree ownership.
4. The DTO binds `route_pair_digest`, normalized `issue_type`, and `change_kind`. Normalization is strip plus Unicode
   case-fold with no internal whitespace rewrite. Exact accepted types are `bug`, `story`, `task`, `sub-task`,
   `improvement`, and `new feature`; `bug` alone maps to `fix`, the rest to `feat`; all others remain blocked for
   review. Branch is independently derived from coordinator key, service kind, and change kind.
5. Repeat the locked pair attestation after fetch/before `prepared`, on ready resume, and in delegate-aware
   `before_run`. Tick nomination is never authority.

### B. Repository binding v1

1. Add `AIDT_REPOSITORY_BINDING_SCHEMA = "aidt-repository-binding-v1"` and one routing-layer observer used by both
   catalog routing and worktree recheck. Its canonical ordered inputs are service ID/kind/catalog checkout, fixed ref,
   full commit, object format, a SHA-256 digest of the canonical origin URL, canonical top-level/common-Git/object-
   directory identity tokens, and sorted required scoring-path object IDs. Identity tokens use canonical path plus
   device/inode when available; absent device/inode is canonical JSON null. The raw origin URL is never logged or
   persisted. Phase-mutable worktree registry, feature refs, and remote-ref history are excluded.
2. The observer retains Frontier 002 caps and immutable regular-blob validation. `observe_catalog` and exported
   `observe_service_binding(settings, service_id)` share the serializer; the latter observes only the named enabled
   service and returns the exact commit/digest without mutation.
3. Under the common-Git lock, immediately after fetch and again before add, resolve the fixed ref, require it equals
   the DTO `checkout_revision`, recompute the binding from that fetched commit, and require it equals both coordinator
   and child binding values. Same-HEAD repositories with different common Git/origin/object identity fail.

### C. Exact runner, locks, and phase deltas

1. The only fetch command is the equivalent fixed argv below, with the canonical service supplied as `cwd` by the
   runner; `-c` safety options precede the command and are separately asserted:

   `git fetch --no-tags --no-recurse-submodules --no-write-fetch-head origin +refs/heads/aidt-prd:refs/remotes/origin/aidt-prd`

   The leading `+` intentionally accepts non-fast-forward production-base correction. Prune, implicit destinations,
   tag fetch, FETCH_HEAD writes, shell, stdin, and alternate refspecs are forbidden.
2. Environment is exact allowlist: `PATH`, `LANG=C`, `LC_ALL=C`, optional `SYSTEMROOT`,
   `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<null-device>`, `GIT_TERMINAL_PROMPT=0`,
   `GIT_ASKPASS=/usr/bin/false`, `SSH_ASKPASS=/usr/bin/false`, `GIT_OPTIONAL_LOCKS=0`, and
   `GIT_NO_REPLACE_OBJECTS=1`. Git options set no replacement objects/fsmonitor/hooks, empty credential helpers,
   protocol default never, and allow only approved origin transport. POSIX `flock` and `/usr/bin/false` are explicit
   capabilities; absence blocks before mutation. Local executable filter/process/smudge/clean,
   `core.sshCommand`, remote upload-pack, or executable hook configuration is a permanent pre-mutation failure.
3. The binary runner has stdin null, no shell, process-group kill/reap, 30 s fetch/10 s local timeouts, 1 MiB stdout
   and 64 KiB stderr caps, and command-specific return-code/parsing rules. Captured channels are never logged or
   persisted. Tests inject file transport; production accepts only HTTPS/SSH origin identity already bound by the
   routing observer.
4. Stable metadata locks are kernel advisory `flock` files beneath the workflow-relative ownership root, keyed by
   SHA-256 of canonical manifest ID or common-Git identity. Unsupported advisory locking is `capability_unsupported`
   and blocks. Kernel release after crash is required; no stale-file stealing/deletion exists.
5. Lock order is always common-Git then manifest. Hold both across re-read S0, fetch, S1, pair/binding proof,
   `prepared`, add, S2, and `ready`; cleanup uses the same order. External Git does not honor the locks, so all phase
   comparisons remain binding.
6. Typed snapshots are S0 before fetch, S1 after fetch/before intent, and S2 after add. S0 -> S1 permits only the
   fixed remote-tracking ref delta; root/index/content, registry, protected occupancy, and every other ref remain
   equal. S1 -> S2 permits only the target local branch plus one target registration; the fixed ref and every
   unrelated ref/registration remain equal. Persist the named delta digest for each phase, not raw output.

### D. Dirty-content proof

1. Root snapshot runs the exact equivalent of `status --porcelain=v2 -z --untracked-files=all`, hashes the index, and
   hashes type plus content/link payload for every tracked-dirty and untracked path returned by the bounded parser.
   Deleted entries receive a canonical missing token. Each open is no-follow and lstat is rechecked around hashing.
2. Maximums are 10,000 paths, 512 MiB total content, 4,096 UTF-8 bytes per canonical relative path, and 1 MiB per Git
   metadata channel. Limit excess blocks rather than sampling. Persist only aggregate digest/count/bytes; never names
   or content.
3. Compare the complete root proof at S0/S1/S2, ready resume, pre-cleanup, and post-cleanup. Porcelain equality alone
   is insufficient.

### E. Stable registry and exact manifest wire format

1. Stable ownership root is `<workflow-path.parent>/.symphony/aidt-worktrees-v1`, independent of workspace root,
   tracker root, service checkout, and cards. A valid first enable atomically creates one idempotent activation marker
   plus per-child ownership records; there is no shared mutable ID list. A never-enabled profile performs no metadata/
   lock/Git/board mutation. Per-child records retain the manifest ID, original workspace root/path, catalog service,
   and tombstone with bounded hashes so disabled/restarted/corrupt/missing-manifest cases remain owned. Bounded
   discovery scans only this stable per-child record directory, never arbitrary roots.
2. Workspace-root change with `prepared`, `ready`, or `removing` records blocks new create/resume but keeps every old
   path guarded. `removed` tombstones also remain owned. Registry and manifest updates share the manifest lock and
   compare-and-swap revision.
3. Canonical JSON is UTF-8, `sort_keys=True`, separators `(',', ':')`, `ensure_ascii=False`, `allow_nan=False`, and one
   trailing newline. Duplicate keys, surrogates/control text, subclasses/coercions, bool-as-int, unknown/missing keys,
   case aliases, and files over 128 KiB fail before use. Files are mode 0600. CAS means locked re-read/exact revision,
   exclusive same-dir temp, flush/fsync, `os.replace`, then directory fsync. Unsupported directory fsync is a bounded
   recorded capability result; file fsync/replace failure always blocks and preserves last good.
4. Common scalar rules: IDs/service/branch use frozen regexes and 256/48/256-byte caps; paths are canonical absolute
   strings <=4096 UTF-8 bytes and must match derived config/catalog values; SHA-1 is 40 lowercase hex; digests and
   identities are 64 lowercase hex; timestamps are UTC RFC3339 seconds but never enter equality digests; exact ints
   are non-boolean `[0, 2^31-1]`; collections have named caps below.

| Object | Exact keys and state rules |
|---|---|
| top-level manifest | `schema`, `manifest_revision`, `state`, `identifier`, `coordinator`, `service`, `kind`, `workflow_identity`, `board_identity`, `workspace_root`, `workspace_path`, `catalog_checkout`, `canonical_service_root`, `common_git_identity`, `object_format`, `route_pair_digest`, `repository_binding_digest`, `route_fingerprint`, `coordinator_fingerprint`, `source_revision`, `catalog_revision`, `branch`, `base_ref`, `base_sha`, `route_scope`, `pre_proof`, `post_proof`, `removal_proof`, `created_at`, `updated_at` |
| enums/constants | schema `aidt-worktree-v1`; state `prepared|ready|removing|removed`; kind `backend|frontend`; object format `sha1`; base ref exact fixed ref |
| route_scope | exact keys `identifier`, `coordinator`, `service`, `kind`, `issue_type`, `change_kind`, `route_pair_digest`, `route_fingerprint`, `coordinator_fingerprint`, `source_revision`, `catalog_revision`, `checkout_revision`, `repository_binding_digest`; all must equal the current pair DTO except workflow state/body |
| snapshot | exact keys `phase`, `observed_at`, `repository_binding_digest`, `root_head`, `root_symbolic_digest`, `root_status_digest`, `root_content_digest`, `root_content_count`, `root_content_bytes`, `registry_digest`, `registry_count`, `protected_digest`, `protected_count`, `refs_digest`, `refs_count`, `base_ref_sha`, `target_ref_sha`, `target_registration_digest`; nullable target values are explicit JSON null |
| pre_proof | exact keys `s0`, `s1`, `fetch_delta_digest`; present for every state |
| post_proof | exact keys `s2`, `create_delta_digest`, `ticket_head`, `registration_digest`, `clean_at_create`, `no_upstream`; null in prepared, non-null in ready/removing/removed |
| removal_proof | exact keys `authority_digest`, `pre_snapshot`, `post_snapshot`, `remove_delta_digest`, `retained_branch_sha`; null in prepared/ready; in removing `post_snapshot`/`remove_delta_digest` are null; fully non-null in removed |

Snapshot counts cap at 2,500 registrations/refs/protected entries. Proof equality ignores `observed_at` only; every
other key is exact. State shapes are exact: prepared has pre only; ready has pre/post; removing has pre/post and a
partial removal intent; removed has all complete. State transitions are absent -> prepared -> ready -> removing ->
removed and each increments `manifest_revision` by one.

### F. Create, resume, and removal recovery

1. Prepared recovery accepts only: no target ref/path/registration, then continue add; or one exact complete clean
   target worktree at base, then verify/finalize ready. Branch-only, path-only, dirty partial, mixed, or unrelated
   delta is permanent preserve-only.
2. Ready resume uses the exact route pair/scope and manifest identity, performs no fetch/add/reset/rebase/checkout or
   cleanup, permits dirty ticket work, and proves base ancestor/no upstream/root/protected/unrelated equality. It does
   not require non-route card bytes/state to equal the creation-time file.
3. Cleanup first verifies an authority capability, exact ready manifest/pair/scope/registration, clean ticket tree,
   root proof, branch SHA, fixed ref, protected/unrelated registry, then writes `removing`. It runs plain
   `git worktree remove <exact-path>` only. No force/prune/raw deletion/branch delete exists.
4. Removing recovery accepts exactly the original exact registration still present, allowing one revalidated plain
   remove retry, or only the owned registration/path absent with branch/fixed ref/root/protected/unrelated state
   unchanged, allowing finalize removed. Mixed shape blocks. Removed is idempotent and keeps the branch/tombstone.

### G. Cleanup capability and lease ownership

1. `CompletionAuthorization` exact fields are `schema=aidt-completion-authorization-v1`, identifier, workflow
   generation digest, route-pair digest, ready manifest revision, issue ID, run ID, attempt kind, owning lease token,
   final transition identity, issuer, issued-at, and authorization digest; all are bounded exact strings/ints using
   the common rules. The DTO alone is not authority.
2. Provisioner receives an injected `CompletionAuthority.verify(token, current_lease, manifest, route_pair)`
   capability. Production Frontier 003 uses deny-all; tests inject a deterministic issuer/verifier. Frontier 004 or
   the later final-delivery controller owns the real issuer only after merge/deploy/dev E2E. Generic Done, a matching
   identifier alone, startup, inactive, blocked, wrong scope/generation, expired/missing/competing lease all preserve.
3. Authorized cleanup requires one exact active owning lease matching issue ID/run ID/token; it rejects a competing
   lease. Terminal reconcile without final authority cancels/stops the worker but neither removes nor marks AIDT
   cleanup complete. A later authorized controller may remove while that same owner is active, then releases lease.
4. Frontier 003 wires no production Core Done removal. It exposes the typed seam and ensures every existing generic
   terminal/startup/reconcile remove receives an owned-preserved result for AIDT. Cleanup primitives are proven only
   with injected test authority in this frontier.

### H. Total delegate and runtime generations

1. Every delegate method returns sealed `DelegateResult`: `UNMANAGED`, `HANDLED(value)`,
   `OWNED_PRESERVED(category)`, or `OWNED_ERROR(category)`. Only exact UNMANAGED permits generic behavior. Exceptions
   translate to OWNED_ERROR once recognition has begun.
2. Recognition covers a current route-managed child; any stable-registry/manifest/tombstone pathname including
   corrupt/symlink/missing manifest; and any catalog registration at a deterministic recorded child path. Missing
   manifest beside a registered known child is owned error. Removed IDs never become generic.
3. `WorkspaceManager.path_for`, `create_or_reuse`, `before_run`, and `remove` use the result; remove gains optional
   identifier/authorization keywords without changing old positional calls. Owned paths never run generic mkdir,
   owner marker, hook, commit, merge, raw recursive removal, startup cleanup, or terminal cleanup.
4. Construct one process-lifetime `AidtWorktreeRuntime` before the first manager. All replacement managers share it.
   Reload atomically publishes an immutable validated generation for future work while keeping stable registry,
   guards, locks, counters, last-known roots, and disabled preservation. Failed reload publishes nothing and globally
   denies dispatch under the existing routing barrier.
5. Each dispatched `RunningEntry` captures its exact `WorkspaceManager`, runtime generation, workflow identity, and
   nominated route-pair digest. Worker create and pre-backend use that manager and require current safe generation/
   pair equality. Manager/root reload cannot split an in-flight lifecycle. Existing RunningEntry constructors keep
   defaults; unmanaged behavior is unchanged.
6. Candidate nomination passes through runtime durable admission after routing filtering. Initial and generic retry
   attempts both re-enter the same admission/create gate; no captured stale Issue/config can bypass it.

### I. Durable failure disposition

1. Stable metadata stores one strict `aidt-worktree-attempt-v1` record per child with identifier, route-pair digest,
   workflow generation, category, disposition `backoff|manual|ready`, exact attempt count, `retry_at`, mutation phase
   `none|prepared|added|removing`, and manifest revision. Route-pair/generation change creates a fresh attempt scope;
   records never edit route-owned card payload.
2. Retryable only before prepared: lock timeout, fetch timeout, and transient fetch command failure use 30 s, 120 s,
   and 600 s delays, maximum three attempts, then manual. Config/card/catalog/binding/base/profile/collision/protocol/
   cap/content/identity failures are manual. Every failure at or after prepared is manual preserve-only. Success marks
   ready. All categories/refs are allowlisted and bounded.
3. Backoff/manual candidates remain locally non-dispatchable across ticks/restart. Core suppresses its generic retry
   scheduler for specialized disposition; due backoff re-enters only through a later tick. A persistence race/failure
   is manual fail-closed. No tracker mutation is allowed in Frontier 003.

### J. Compatibility and amended proof matrix

1. Accepted compatibility is observable, not byte-for-byte: the result field is last/defaulted; old positional and
   keyword construction still works; malformed normalization remains canonical; repr/equality change is accepted but
   repr exposes counts only. Filter default stays empty and old calls/order work. Lazy imports remain cycle-free and
   do not load worktree/Git modules while disabled.
2. Never-enabled profile performs no metadata/lock/Git/network/board action and every unmanaged manager signature,
   hook, owner marker, create/reuse, remove, and Done path is unchanged. After first activation, preservation of known
   AIDT identities takes priority over generic parity.
3. Add binding tests for atomic pair/coordinator/child-set drift; post-fetch observer; S0/S1/S2 unrelated deltas;
   exact argv/env/forbidden commands; same-common-Git two-process lock/crash release; dirty-file same-status content
   mutation; golden manifest/proof/CAS/duplicate-key/bool/oversize/symlink/case/temp-crash; root A -> B reload/restart;
   tri-state exception/corrupt/missing/removed no-fallback; generation races at nomination/create/before-run/reconcile/
   retry; deny-all generic Done; valid/invalid test authority; removing crash seams; durable backoff/manual restart;
   accepted issue types/change-kind drift; Git hook/filter sentinel non-execution; default-off/import/unmanaged parity.
4. Product scope additionally permits `aidt_routing/git_objects.py`, its focused tests,
   `orchestrator/entries.py`, and run-registry-facing integration tests. No live profile or AIDT checkout is used.

## Binding Amendment 2 - Recheck Closure

This amendment closes the remaining recheck findings and supersedes any looser spelling in Amendment 1.

### K. Shared route locks and pair-wide reread

`apply_route_resolutions` already takes every target card's `_ticket_lock_path` in sorted projection order. The pair
loader must take those same exact coordinator/child locks in the same case-folded identifier order. While both are
held it performs one pair-wide cycle: read/hash/lstat coordinator, read/hash/lstat child, then reread/hash/lstat both
and require the two complete observations equal before parsing the second bytes. The digest is produced only from
that second stable pair. No alternative board lock or per-file window is allowed. Tests inject coordinator replacement
and child-set replacement between every pair-wide seam and assert no Git command or generic fallback.

### L. Exact fetch vector and origin parser

The fetch process `cwd` is the canonical service root and its complete argv, in order, is:

```text
git
--no-optional-locks
--no-replace-objects
-c core.fsmonitor=false
-c core.hooksPath=/dev/null
-c credential.helper=
-c protocol.allow=never
-c protocol.https.allow=always
-c protocol.ssh.allow=always
-c protocol.file.allow=never
-c filter.lfs.process=
-c filter.lfs.smudge=
-c filter.lfs.required=false
fetch
--no-tags
--no-recurse-submodules
--no-write-fetch-head
origin
+refs/heads/aidt-prd:refs/remotes/origin/aidt-prd
```

No equivalent spelling is accepted. The null device is exact `/dev/null`; POSIX capability checks occur before
metadata creation. The preflight origin command uses the same global options and exact tail
`remote get-url --all origin`; it must return exactly one bounded ASCII line. Accepted origin is either `https://`
or `ssh://`, with nonempty hostname and absolute nonempty repository path, optional numeric port, no password, query,
fragment, control, backslash, percent-encoded separator, dot segment, or scp shorthand. HTTPS forbids userinfo; SSH
permits one bounded username. The normalized scheme/host-lowercased/default-port-elided/path-preserved URL is hashed
with `SHA256("aidt-origin-v1\0" + value)` and only that digest enters the repository binding. Every create/resume/
prepared-recovery/removing-recovery/cleanup failure fixture shares one command-spy assertion proving this exact vector
when expected and proving all forbidden Git/generic operations absent.

### M. Per-field wire rules

All objects below use Amendment E's canonical encoding and durable-write protocol. `str` always means exact JSON
string, no subclass/coercion, surrogate, or Unicode control; `int` means exact non-boolean JSON integer. Unless a row
says nullable, the field is required and non-null. Every field participates in byte/CAS equality; timestamps alone
are excluded from semantic proof digests.

| Fields | JSON type and exact rule |
|---|---|
| `schema` | str ASCII enum <=64 bytes, object-specific exact value |
| `manifest_revision`, `registry_revision`, `record_revision` | int 1..2147483647, increments exactly one |
| `state` | str exact state enum from Amendment E |
| `identifier`, `coordinator` | str canonical card/child grammar, 1..256 ASCII bytes |
| `service` | str service grammar, 1..48 ASCII bytes |
| `kind` | str `backend|frontend` |
| `workflow_identity`, `board_identity`, `common_git_identity` | str exactly 64 lowercase hex, domain-separated SHA-256 of the derived canonical identity |
| `workspace_root`, `workspace_path`, `canonical_service_root`, `manifest_path` | str canonical absolute no-control path, 1..4096 UTF-8 bytes; exact derived value |
| `catalog_checkout` | str relative one-segment catalog path, 1..256 UTF-8 bytes, no slash/backslash/dot segment |
| `object_format` | str exact `sha1` |
| every `*_digest`, fingerprint, source/catalog revision, transition identity | str exactly 64 lowercase hex unless the source contract fixes a stricter canonical token |
| completion `run_id`, `owning_lease_token` | str exactly 32 lowercase hex and byte-equal, matching the frozen completion-authorization contract |
| `base_ref` | str exact `refs/remotes/origin/aidt-prd` |
| `base_sha`, `root_head`, `ticket_head`, `retained_branch_sha`, `base_ref_sha`, `target_ref_sha` | str exactly 40 lowercase hex; target ref is the only nullable one |
| `branch` | str exact derived branch grammar, 1..256 ASCII bytes |
| `issue_type` | str exact normalized accepted enum <=16 bytes |
| `change_kind` | str `feat|fix` |
| `phase`, mutation phase, disposition, category, issuer, attempt kind | str object-specific closed ASCII enum <=64 bytes |
| `created_at`, `updated_at`, `observed_at`, `issued_at`, `retry_at` | str exact 20-byte UTC `YYYY-MM-DDTHH:MM:SSZ` |
| counts/revisions/attempt | int with named cap: paths 10000, bytes 536870912, refs/registry/protected 2500, attempts 3 |
| `clean_at_create`, `no_upstream`, `tombstone` | exact JSON bool |
| explicitly nullable snapshot/removal fields | exact JSON null or the single field-specific type; absence is invalid |

Top-level manifest comparison is exact for every field. `updated_at` and nested `observed_at` may change only on a
valid revision transition and are excluded only from semantic delta hashes. Route scope fields use the exact scalar
rows above. Snapshot digest fields are 64-hex; snapshot counts use their named caps; snapshot `base_ref_sha` is always
non-null and `target_ref_sha`/`target_registration_digest` alone may be null. Proof booleans must be true. Partial
removal proof has exact null post fields; complete removal proof has exact snapshot/digest types.

For cleanup DTOs, `workflow_generation`, `final_transition_identity`, and `authorization_digest` are exactly 64
lowercase hex; `issue_id` is an exact canonical file-tracker child ID <=256 ASCII bytes; existing run-registry
`run_id` is exactly 32 lowercase hex and is also the `owning_lease_token` (the two fields must be byte-equal rather
than independently invented). `issuer` is exact `aidt-stage-controller-v1` in production and a named <=64-byte test
issuer only through dependency injection. Authorization scalar nullability is none: every field is required.

Stable sidecar schemas are also closed:

| Object/path | Exact keys |
|---|---|
| `ACTIVATED.json`, schema `aidt-worktree-activation-v1` | `schema`, `registry_revision`, `workflow_identity`, `created_at`, `updated_at` |
| `ownership/<identifier>.json`, schema `aidt-worktree-ownership-v1` | `schema`, `record_revision`, `identifier`, `service`, `workspace_root`, `workspace_path`, `manifest_path`, `route_pair_digest`, `manifest_revision`, `tombstone`, `created_at`, `updated_at` |
| `attempts/<identifier>.json`, schema `aidt-worktree-attempt-v1` | `schema`, `record_revision`, `identifier`, `route_pair_digest`, `workflow_generation`, `category`, `disposition`, `attempt`, `retry_at`, `mutation_phase`, `manifest_revision`, `created_at`, `updated_at` |

Ownership `manifest_revision` is exact int and attempt `manifest_revision` is int or null only before prepared.
Attempt `retry_at` is timestamp only for backoff and null for manual/ready. Activation has no per-child list.
Directory discovery caps at 2,500 exact regular entries, rejects unknown names/types/symlinks, and case-folds every
identifier; duplicate/case-colliding ownership, attempt, or manifest names make the entire activated registry owned-
error and disable nomination. Golden fixtures bind every canonical byte string and digest.

### N. Ignored-state content proof

The exact root status command is
`git status --porcelain=v2 -z --untracked-files=all --ignored=matching`. In addition to dirty/untracked entries, each
ignored path is hashed. An ignored directory is recursively enumerated with sorted byte-safe relative names using
no-follow `scandir`; directories, regular files, and symlink payloads receive distinct domain tokens. The same 10,000
path/512 MiB total/4,096-byte relative-path caps cover dirty, untracked, and ignored content together; other file
types or limit excess block. `.git` and every registered worktree administrative path are rejected from content
enumeration. S0/S1/S2/resume/cleanup compare the aggregate, catching a same-status ignored-file mutation.

### O. Removing authority and exact crash fixtures

A `removing` recovery that would execute `git worktree remove` again must obtain a freshly verified
`CompletionAuthorization` and the same exact active owning lease under current route/manifest generation. Expired or
missing authority can only preserve. If the exact owned registration/path is already absent, recovery performs no Git
mutation and may finalize the previously authorized intent after proving the recorded authority digest, branch SHA,
fixed ref, root, protected occupancy, and unrelated registrations unchanged.

Named fault fixtures and restart outcomes are binding:

- `after_forced_fetch_before_prepared`: no manifest/ref/worktree; retry starts from a new S0.
- `after_prepared_fsync_before_add`: exact prepared with no target artifacts; recovery performs one add.
- `after_add_before_verification`: exact prepared plus complete target; recovery verifies then writes ready.
- `after_verification_before_ready_fsync`: same exact complete target; recovery re-verifies then writes ready.
- `after_removing_fsync_before_remove`: exact registration remains; new valid authority permits one plain retry.
- `after_physical_remove_before_removed_fsync`: target absent and branch retained; recovery proves and finalizes only.

Every fixture runs the shared command/fallback spy. Property tests use deterministic generated scalars without adding
a dependency: unknown/missing keys, exact/boundary-plus-one lengths/counts, bool-as-int, Unicode normalization/case
aliases, controls/surrogates, duplicate JSON keys, randomized key order, and canonical encode-decode-encode byte
equality across manifest, proof, activation, ownership, and attempt records.

### P. Durable attempt protocol and clock

Attempt records live only at the stable `attempts/<identifier>.json` path, use the exact schema above, and share the
manifest lock, canonical encoding, exclusive temp/fsync/replace/directory-fsync, exact revision CAS, case-collision
rules, and 128 KiB cap. Runtime nomination captures the successfully read `record_revision`; admission/create re-read
and require that exact revision before action. A persistence failure flips a process-global fatal worktree circuit
breaker and admits no child for the rest of that process; it does not claim durable manual state. A restart may make
one new bounded persistence attempt, then closes again if storage remains unavailable.

The clock is injected UTC wall time normalized to whole seconds. Backoff is due only when `now >= retry_at`; backward
clock movement safely extends blocking, forward movement makes the record due, invalid/non-UTC time is permanent
runtime failure, and internally written retry times may never exceed now plus 600 seconds. Due admission atomically
increments record revision/attempt before starting Git work, so a crash cannot launch two attempts from one revision.
Manual and ready records have null retry time and never auto-admit; route-pair/workflow-generation change creates a
new record revision with attempt zero only after the new pair is atomically attested.

## Binding Amendment 3 - Final Recheck Closure

1. Product origin policy and argv remain HTTPS/SSH-only and byte-exact. Temporary Git tests never pass a file URL
   through product parsing and never use a test-only argv. Fixtures configure a canonical
   `https://fixture.invalid/repository.git` origin; an injected binary-runner seam first asserts the complete
   production fetch argv/environment/origin digest, then simulates only that fetch side effect by moving the fixture
   `refs/remotes/origin/aidt-prd` to the commit supplied by the local bare fixture. Every other Git command, snapshot,
   worktree add/remove, ref/registry proof, and dirty-content proof executes against the real temporary repository.
   The command spy records the requested production fetch, not the test double's fixture setup commands. No file
   origin reaches a product parser or runner.
2. `workflow_generation` in every attempt and authorization object is an exact required JSON string of 64 lowercase
   hex: `SHA256("aidt-workflow-generation-v1\0" + canonical validated safety-profile bytes)`. Admission, route-pair
   scope reset, reload, and CAS compare this exact grammar/value.
3. The final two sentences of Amendment P are replaced: `manual` never admits; non-due `backoff` never admits; due
   `backoff` atomically consumes the next bounded provisioning attempt; `ready` never schedules or increments a
   provisioning attempt but does admit the route-managed worker into exact ready-resume plus pre-backend attestation.
   A ready record with missing/mismatched manifest is manual owned failure. A restart fixture proves ready admission
   runs no fetch/add, resumes once, and reaches the backend barrier once.

## Binding Amendment 4 - Runtime Plan-Attack Closure

This amendment closes all runtime-plan attack findings and supersedes any conflicting runtime wording above.

1. The runtime constructor accepts an optional provisioner factory whose default is `None`. It resolves the default
   provisioner lazily only inside the first valid enabled publication. Importing, constructing, or disabled-publishing
   the runtime must not load provisioner, manifest, or Git-state modules or create/probe stable metadata. The eventual
   call remains exact: `factory(config, settings, clock=clock)`.
2. Recognition precedes mutating generation gates. `path_for` alone may return `HANDLED(recorded_path)` under a
   disabled or rejected generation after an exact durable manifest plus ownership/tombstone pair validates; this is
   non-mutating and prevents generic derivation. Admission, create/reuse, before-run, and remove remain final
   preserve/error. Corrupt, missing, conflicting, or partial durable evidence is owned error. A syntactically canonical
   child is unmanaged only after the bounded route loader returns `None`; a zero-loader negative control must use a
   non-child generic identifier.
3. Ready admission re-reads the exact ready manifest, non-tombstoned ownership record, and ready attempt under the
   manifest lock. Revisions, route pair, workflow generation, identifier, path, and state must align before resume is
   handled. Missing or mismatched evidence becomes a persisted manual owned failure; candidate admission never calls
   provisioner prepare and never copies its private helpers.
4. Publication validation, clock validation, registry activation, and factory construction complete off to the side.
   Any failure publishes nothing and preserves the exact current generation/provisioner. The caller applies one
   bounded rejection; a durability or invalid-clock failure latches fatal exactly once. Equivalent publication after
   fatal may return the current DTO, but a changed or disabled publication raises the latched bounded failure and may
   not replace manager-facing state.
5. A successful provisioner prepare increments create or resume exactly once by `admission.action` before the
   publication-token postcheck. This records the real durable transition even when a concurrent reload makes the
   returned result `OWNED_ERROR("scope_changed")`; `created_now` does not classify the counter.
6. Route-loader `None` before recognition is unmanaged. Production `AidtRoutingFailure` for a canonical child maps to
   `OWNED_ERROR("card_invalid")`; bounded `AidtWorktreeFailure` preserves its allowlisted category/ref; every other
   exception after recognition maps to `OWNED_ERROR("internal_error")`. Factory exceptions are atomic publication
   failures, not delegate results.
7. Fatal tests cover activation, initial write, consume/reset, provisioner `persistence_failed`, and invalid/non-UTC
   clock. Repeated fatal delegate/reload rejection never inflates failure counts. Health snapshots copy only locked
   memory: no clock, route, filesystem, registry, manifest, Git, provisioner, tracker, or network call. Runtime product
   functions remain at most 50 lines with nesting at most four, verified by executable/static gates.
8. Registration-only recognition with no current route and no durable registry/manifest/attempt/ownership record is
   deferred because no public observer exists. Runtime must not import private Git-state parsers to simulate it.
   Current-route or durable-record ownership remains final; a later bounded public observer may restore the broader
   orphan-registration requirement.
9. Idempotence uses a private immutable material-publication key built from validated settings/generation plus the
   exact immutable config fields consumed by runtime/provisioner. It never relies only on equality of the previously
   stored shallowly frozen `ServiceConfig.raw`; the public DTO retains `config` for compatibility and Core owns it
   without mutation.
10. Concurrent attempt initialization is executable: one contender consumes revision 2, every CAS loser remains a
    bounded owned outcome, and only one durable provisioning admission exists.

## Binding Amendment 5 - Truthful Invalid-Ready Failure State

The first runtime GREEN attempt exposed one public manifest-contract gap and stopped without a workaround. A ready
attempt truthfully has `mutation_phase="added"` and `manifest_revision=2`; when runtime revalidation finds missing or
mismatched ready manifest/ownership evidence, `next_failure_record(..., "registry_invalid", "added", 2, now)` must
produce a manual record that preserves that exact phase/revision. The current `_valid_manual_attempt` rejects this
shape before persistence, contradicting Amendments 3 and 4.

Authorize only this root correction:

1. Add an executable manifest regression proving a public ready attempt becomes a persisted manual
   `registry_invalid` record with the same `added`/2 truth, next exact record revision, null retry time, unchanged
   attempt/scope/created time, and monotonic updated time.
2. Extend the manual phase/revision validator to accept exact `added`/2. Do not accept arbitrary revisions, change
   ready admission, weaken other state shapes, add a runtime-only constructor, or fabricate `prepared`/1 or
   `removing`/3.
3. Re-run manifest/contract/provisioner/recovery compatibility before resuming runtime GREEN. This amendment expands
   the runtime slice only to `manifest.py` and its focused manifest test for this exact public-seam repair.

## Binding Amendment 6 - Worker-contract and commit partition

This amendment supersedes only the earlier single-ticket `Cohesive File Scope` and Build ownership. It does not
change the frozen product contract, tests, evidence, GOAL state, QA verdict, run state, or live-mutation prohibition.
Ticket 003 is now a rollup. One worker ticket owns one deep module/interface contract:

| Ticket | Owned interface | Dependency | Work route |
|---|---|---|---|
| 003a | route-child nomination and stable pair attestation | 002 | historical umbrella |
| 003b | default-off profile, branch/path, and public identity | 003a | historical umbrella |
| 003c | canonical manifest/ownership/attempt persistence | 003b | historical umbrella |
| 003d | bounded Git runner, observation, mutation deltas, and recovery proof | 003b, 003c | historical umbrella |
| 003e | provision/resume/guard/authorized-cleanup lifecycle | 003a, 003c, 003d | historical umbrella |
| 003f | process generation, admission, delegation, and bounded health | 003e | historical umbrella |
| 003g | WorkspaceManager/Core attempt-custody integration | 003a, 003f | historical umbrella |
| 003h | atomic runtime-generation/manager publication | 003f, 003g | correction Build |
| 003i | shipped default-off operator profile and config proof | 001, 002, 003b | docs/config Build |
| 003v | aggregate release verification and closure recommendation | 001a verification, 003a-003i | release verification |

Tickets 003a-003g describe already executed seams and evidence. Their current single-module implementations and
tests exceed the rough five-file/500-net-line Build limit, so they are explicitly historical umbrellas rather than
new Build prompts; relabeling them as bounded would hide the process deviation. The remaining Build tickets 003h
and 003i each own at most three files and target at most 500 net lines. Both are built and remain pending 003v fresh
verification. Reopened Frontier 001 correction 001a is likewise built but requires fresh focused/affected/static/
whitespace proof before Frontier 001 is shown closed again or 003v recommends closure. Any expansion requires
another ticket.

### Commit sequence before closure

Create coherent commits in dependency order, staging exact hunks when a file also contains unrelated or later work:

1. `003a`: route nomination/filter and route-pair dispatch-attestation interface plus owned tests.
2. `003b`: worktree profile/identity contract plus contract tests.
3. `003c`: canonical durable records, locks, registry, attempt admission, and manifest tests.
4. `003d`: repository binding/Git runner/state/recovery proofs plus owned tests.
5. `003e`: provisioner lifecycle, temporary-Git support, and provisioner tests.
6. `003f`: process runtime/lazy facade and runtime tests.
7. `003g`: WorkspaceManager/RunningEntry/Core custody integration plus owned tests; compatibility-only fixture edits
   travel in this commit only when they directly prove that interface.
8. `003h`: atomic publication correction and its red/green regression as a separate commit after 003g.
9. `003i`: operator example/reference/validation proof as a docs/config commit; no activation or secret enters it.
10. `003v`: verification evidence and, only if every gate is green, the closure-state commit.

Before each commit, audit staged paths against its ticket and run that ticket's focused proof plus whitespace. Before
003v closure, run the full preserved verification matrix and confirm generated `uv.lock`, unrelated docs/source,
GOAL/run-state/Z changes, and live actions are absent unless independently authorized. This sequence records the
historical size deviation without pretending the original omnibus change was one compliant Build ticket.
