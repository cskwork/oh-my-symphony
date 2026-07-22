# Plan attack - Frontier 003 AIDT Worktree Provisioner

Date: 2026-07-21
Gate: adversarial pre-build review
Inputs: `PLAN.md`, `exploration/frontier003-route-dispatch-contract.md`

## Decision

**FAIL.** The plan has the right worker-time, fail-closed boundary, but it does not yet freeze enough identity and
authority to prevent a stale route pair, reloaded workspace manager, or generic Done reconciliation from acting on
the wrong worktree. The amendments below are required before the first red test.

## MUST amendments

### MUST-1 - Attest the coordinator and child as one atomic pair

`load_route_dispatch_contract` cannot attest the child card alone. Freeze one board-storage operation that acquires
the existing AIDT routing board lock, opens both canonical regular files, reads bounded bytes for coordinator and
child, and validates both while the lock is held. It must prove:

- exact coordinator and child source tuples, route schema, owned states, and current source revisions;
- the coordinator's current routed-service set contains exactly the nominated child service;
- the child's coordinator/service, coordinator fingerprint, catalog revision, source revision, route fingerprint,
  and repository binding agree with the coordinator projection from the same snapshot;
- a bounded `route_pair_digest` is computed from the two canonical byte digests plus their declared revisions.

Return `route_pair_digest` in `AidtRouteDispatchContract`, bind it in the manifest, and repeat the same paired read
immediately before backend construction. External writers that do not honor the process lock must be detected by
pre/post file identity plus byte-digest comparison; an unstable pair fails closed. Tick nomination remains a hint,
not an attestation. A set of child identifiers alone must never authorize provisioning.

### MUST-2 - Recompute repository binding after the forced fetch

"Proves binding equality" is underspecified. Under the common-Git lock, after fetch:

1. Resolve `refs/remotes/origin/aidt-prd` to one lowercase full object ID and require it equals
   `checkout_revision`.
2. Recompute the repository binding from that fetched commit using one shared canonical function exported from the
   routing layer. Its version, ordered inputs, evidence caps, object format, catalog service/kind/checkout, fixed ref,
   commit, and immutable commit-object evidence must be frozen in the plan.
3. Require the recomputed digest, not a copied card/catalog string, to equal the child and coordinator binding.
4. Re-attest the route pair after the fetch and before writing `prepared` or running `git worktree add`.

A SHA equality check without post-fetch binding recomputation is insufficient: it proves the ref, not that the
service/catalog/evidence tuple still describes the repository being mutated.

### MUST-3 - Use the exact forced refspec and exact lock protocol

Freeze fetch argv as:

```text
git fetch --no-tags --no-recurse-submodules --no-write-fetch-head origin \
  +refs/heads/aidt-prd:refs/remotes/origin/aidt-prd
```

The runner supplies the canonical repository as `cwd`, `GIT_TERMINAL_PROMPT=0`, a bounded sanitized environment,
and no shell. No alternate refspec, implicit destination, prune, tag fetch, or FETCH_HEAD write is accepted.

Freeze the cross-process lock identity and order. Resolve and validate the canonical common-Git directory before
mutation, derive a non-reversible lock key from canonical path plus device/inode/object-format identity, and place
the lock under the stable metadata root. Acquire **common-Git lock first, manifest lock second** everywhere; never
invert that order. Hold both across the second snapshot, fetch, post-fetch binding proof, `prepared`, worktree add,
verification, and `ready`; cleanup uses the same order. The advisory lock protects Symphony peers only, so every
external-Git-sensitive assumption still needs an immediate pre-mutation reread and post-mutation proof.

### MUST-4 - Make manifest discovery independent of mutable workspace root

The proposed `<workspace-root>/.symphony-aidt-worktrees/...` location cannot satisfy the disable/restart guard when
`workspace.root` changes. Freeze one stable ownership root derived from the immutable workflow-config identity (or
another already stable Symphony state root), not from `workspace.root`, tracker root, service checkout, or issue
card. Store the configured workspace root and exact ticket path inside the manifest.

After a workspace-root reload, the delegate must still discover and own every old manifest. Root drift blocks
resume, reuse, replacement, and generic cleanup; it preserves the old path and surfaces a bounded mismatch. A new
root must not permit a second worktree for the same child. Add restart/reload tests covering enabled-to-disabled and
old-root-to-new-root discovery. If no stable runtime identity exists, configuration must add an explicit absolute
`metadata_root`; silently deriving it from the mutable workspace root is not acceptable.

### MUST-5 - Freeze the manifest and proof schema before implementation

The prose field list is not an exact schema. Add a table with every key, exact JSON type, maximum length/count,
canonical form, state availability, and comparison rule. At minimum the top-level exact-key set must cover:

```text
schema, manifest_revision, state,
identifier, coordinator, service, kind,
workflow_identity, board_identity,
workspace_root, workspace_path,
catalog_checkout, canonical_service_root,
common_git_identity, object_format,
route_pair_digest, repository_binding_digest,
route_fingerprint, coordinator_fingerprint,
source_revision, catalog_revision,
branch, base_ref, base_sha, route_scope,
pre_proof, post_proof, removal_proof
```

Freeze `route_scope` as an exact object, not a free-form map. Freeze proof objects as exact-key digests and bounded
scalars rather than raw Git output:

- `pre_proof`: root HEAD, digested symbolic-ref/status/registry/protected occupancy, target ref/path absence;
- `post_proof`: the same unchanged-root digests plus ticket HEAD/branch, registration digest, clean-at-create proof,
  and no-upstream proof;
- `removal_proof`: typed authority identity, pre-cleanup registration/cleanliness digest, exact registration absence,
  and unchanged root/protected-occupancy digests.

State shapes are exact: `prepared` has pre-proof and null post/removal proof; `ready` has pre/post and null removal;
`removed` has all three. Define the canonical JSON bytes, revision/CAS comparison, file mode, maximum file size,
temp-file naming, fsync error policy, and unsupported-directory-fsync behavior. No implementation may invent fields.

### MUST-6 - Replace `completed_identifier` with typed cleanup authority

An optional identifier string plus card state `Done` is not cleanup authority. It is replayable, has no run owner,
and can be produced before merge/deploy/dev E2E. Introduce a frozen, bounded cleanup-authority DTO containing at
least identifier, workflow generation, run/attempt identity, owning lease token, expected ready-manifest revision,
and the final stage transition identity. Validate all fields atomically against the run registry, card/route pair,
and manifest immediately before removal.

Terminal reconcile cleanup must run while the **matching active owning lease** is still held. Replace the plan's
"no active run lease" condition with "no competing lease and one exact active owner matching the authority"; core
ordering is terminal transition -> authorized cleanup under that lease -> lease release. Startup reconciliation,
manual Done, wrong/expired/missing lease, and a generic `Done` callback preserve the AIDT worktree.

Frontier 003 may implement and fixture-test the cleanup primitive, but it must not wire generic Done to AIDT removal.
The later stage controller can issue final authority only after the goal's merge/deploy/dev-E2E gate. The feature
branch is never deleted. `removed` is written only after exact plain worktree removal and proof.

### MUST-7 - Define reload ownership and WorkspaceManager generation semantics

The captured `WorkspaceManager`/`cfg` can outlive a workflow reload. Freeze one versioned delegate-provider contract:

- a successful reload constructs and atomically publishes one immutable workspace/delegate generation for future
  nominations and attempts;
- each dispatched attempt captures that generation and matching workflow identity once, then revalidates it at both
  preparation and before-backend barriers;
- in-flight owners retain their captured guard; a newly disabled or invalid generation stops nomination but cannot
  detach ownership of manifests or expose them to generic removal;
- a failed reload publishes no partial manager/delegate; last-known-good config may remain readable, but dispatch is
  denied as the existing routing gate requires;
- changing workspace root never replaces the stable manifest registry and never makes an old AIDT path unmanaged.

Specify whether core replaces the whole manager or swaps only its provider. The recommended minimal design is one
generic `WorkspaceManager` with an atomic versioned delegate provider and a stable manifest guard. Add a race test
for reload between tick nomination, create, before-run, terminal reconciliation, and retry.

### MUST-8 - Make ownership final at every generic fallback seam

The delegate's tri-state needs exact meaning. Use a typed disposition such as `UNMANAGED`, `HANDLED`, or
`OWNED_PRESERVED/ERROR`; `None` is too easy to conflate with a handled method's normal return. Recognition comes from
either a canonical managed card **or any discoverable manifest**, including disabled/reloaded profiles and removed
manifests. Once recognized, no generic mkdir, hook, auto-commit, merge, raw recursive removal, or owner-marker path may
run. Cover `path_for`, create/reuse, before-run, terminal reconcile, retry, shutdown/startup cleanup, and explicit
remove—not only the happy worker path.

### MUST-9 - Tighten compatibility into testable guarantees

Replace "byte-for-byte compatible" with observable contracts and freeze them:

- `AidtRoutingResult` adds its defaulted field last; old positional/keyword construction and malformed-result
  normalization retain behavior, while repr/equality changes are explicitly accepted or hidden;
- `filter_routing_candidates` defaults to an empty provisionable set and preserves input order and old call sites;
- public facade import permutations remain cycle-free and do not import Git/worktree modules when the feature is off;
- existing `WorkspaceManager` positional signatures, return values, unmanaged hooks, owner marker, create/reuse, and
  Done cleanup remain unchanged when the delegate says `UNMANAGED`;
- a never-enabled profile performs no manifest creation, Git/network operation, lock creation, or board mutation;
- enabled-to-disabled with an existing manifest performs only bounded ownership discovery and preservation;
- config reload failure, missing board, routing-disabled mode, retries, and process restart are covered by golden
  default-off/unmanaged tests.

The apparent tension between default-off parity and the disabled manifest guard must be resolved explicitly with
the stable registry: no prior manifest means exact old behavior; a prior manifest means preservation takes priority.

## SHOULD amendments

1. Add crash-point fixture tests after forced fetch, after `prepared`, after successful add, after verification, and
   after physical removal before `removed`; every restart must choose one documented exact recovery outcome.
2. Add property tests for manifest unknown keys, bool-as-int confusion, Unicode/case aliases, size/count caps, and
   canonical JSON round trips.
3. Test two processes, not only threads, contending for one common-Git directory; prove lock order and absence of a
   second feature ref/worktree.
4. Record only bounded category, canonical identifier/service, manifest revision, and workflow generation in health;
   never expose lock keys or absolute metadata/service/workspace paths.
5. Add a command-spy assertion over every create/resume/recovery/cleanup failure proving the forbidden Git command
   set and generic fallback set remain empty.
6. Make file and directory durability expectations platform-explicit: unsupported directory fsync is a classified
   capability result, while file flush/fsync/replace failures always block.

## Rejected interpretations

- **Child-only re-read:** rejected because a coordinator reroute can leave a still-well-formed child stale.
- **Use the card's repository digest after fetch:** rejected because it is assertion reuse, not recomputation.
- **No active lease during cleanup:** rejected because terminal reconciliation then has no exclusive owning actor.
- **Generic Done authorizes removal:** rejected because the requested workflow is not complete until deploy and dev
  E2E; Done alone can erase the worktree too early.
- **Manifest under current workspace root:** rejected because a root reload loses ownership discovery.
- **Replace WorkspaceManager on reload without handoff:** rejected because in-flight and old-manifest paths can fall
  through to generic behavior.

## Gate result

**FAIL - amend `PLAN.md` with MUST-1 through MUST-9, add their named red tests, and rerun this attack before Build.**

## Amendment Recheck

Rechecked `Binding Amendment 1` only. The amendment supersedes the conflicting original clauses, but Build remains
blocked where an implementation would still have to invent an identity, durability, or authority rule.

### MUST-1 - Atomic coordinator/child attestation: FAIL

The pair content contract, child-set membership, projection digest, bounded reads, and repeated worker barriers are
now strong. The remaining blocker is lock interoperability: Amendment A uses coordinator and child file locks, while
the routing producer is described elsewhere as a whole-batch/board-locked writer. Per-file locks are atomic only if
**every** route writer acquires the same two locks in the same order. Freeze either the shared routing board lock for
the paired read, or explicitly require all batch writers to acquire these exact file locks. Also require one
pair-wide before/read/after reread of both byte hashes while the shared lock is held; per-file lstat windows do not
exclude a coordinator change between the two reads.

### MUST-2 - Post-fetch repository-binding recomputation: PASS

The shared versioned observer, immutable scoring-object inputs, canonical origin-URL SHA-256 input, and two
post-fetch recomputations close both copied-digest and same-HEAD/different-repository risks. Raw origin data remains
outside persistence and logs.

### MUST-3 - Exact forced refspec and common-Git lock: FAIL

The leading-`+` refspec, `cwd`, lock location/order/lifetime, process locking, phase snapshots, and allowed deltas pass.
POSIX `flock` and `/usr/bin/false` are now explicit pre-mutation capabilities, so askpass and advisory-lock fallback
are closed. The runner vector is still not exact: "equivalent fixed argv", unspecified ordered `-c` safety options,
and `<null-device>` leave security-sensitive values to implementation. Freeze the complete argv array in order,
resolve the POSIX null device to one exact value, and freeze the exact approved-origin parser. The fetch command
itself must have no equivalent alternate spelling.

### MUST-4 - Workspace-root-independent discovery: PASS

The workflow-relative activation marker plus per-child ownership records are independent of workspace/tracker/
service/card roots, retain original roots and tombstones, avoid one shared mutable ID list, survive disable/restart/
root A -> B, and prevent a second create. Never-enabled behavior is separately preserved.

### MUST-5 - Exact manifest/proof schema: FAIL

The exact-key sets, state shapes, canonical JSON, CAS, file mode, fsync, size cap, and proof phases are materially
better. The table still does not assign an exact JSON type, nullability, byte/count cap, and comparison rule to each
field. Examples left ambiguous include `workflow_identity`, `board_identity`, `common_git_identity`, revision and
fingerprint fields, every snapshot scalar, and timestamps. Add a per-field wire table (including the activation
marker, per-child ownership/tombstone record, and attempt record) so the implementation cannot choose string versus
object, nullable versus required, or reuse one generic cap. Define duplicate/case collision behavior across per-child
records in that same schema.

### MUST-6 - Typed cleanup authority and active lease: FAIL

The production deny-all capability, exact authorization DTO, no generic Done wiring, and matching active owning lease
close the ordinary cleanup hole. Removing recovery remains ambiguous: F.4 permits a "revalidated" plain remove retry
but does not explicitly require a newly verified `CompletionAuthorization` and the same active owning lease. Freeze
that destructive retry requirement. An already-absent exact worktree may finalize the recorded removing intent
without another Git mutation, but it must not issue a new remove after the authority/lease has expired.

### MUST-7 - WorkspaceManager reload generations: PASS

One process-lifetime runtime, immutable published generations, manager capture in `RunningEntry`, failed-reload deny,
stable guards, and initial/retry admission now give old and new managers an explicit ownership handoff.

### MUST-8 - Final ownership at fallback seams: PASS

The sealed four-way `DelegateResult`, recognition by current card/registry/manifest/tombstone/registration, exception
conversion after recognition, and explicit ban on every generic mutation/cleanup fallback close this requirement.

### MUST-9 - Compatibility: PASS

Observable result/filter/import/manager compatibility is frozen. Never-enabled profiles remain inert; previously
activated identities intentionally prioritize preservation. The amended race and golden parity matrix is sufficient
for the compatibility contract.

### SHOULD-1 - Named crash-point fixtures: FAIL

`temp-crash` and `removing crash seams` are too broad. Name fault injection and expected restart outcomes after
forced fetch, persisted prepared, successful add, successful verification before ready, persisted removing, and
physical removal before removed.

### SHOULD-2 - Manifest property tests: FAIL

The matrix names duplicate keys, bool, oversize, symlink, and case cases, but not property-based generation, Unicode
aliases/surrogates across every scalar, or canonical encode-decode-encode byte equality. Add those explicit tests.

### SHOULD-3 - Cross-process common-Git contention: PASS

The amended matrix requires same-common-Git two-process locking and crash release.

### SHOULD-4 - Bounded health: PASS

The original bounded category/ref contract plus Amendment I's allowlisted categories and the persistent no-output/
no-path rule keep health bounded and sanitized.

### SHOULD-5 - Command/fallback spy on every failure: FAIL

The matrix checks exact argv/env, forbidden commands, sentinels, and no-fallback categories, but it does not require
the spy assertion for **every** create/resume/prepared-recovery/removing-recovery/cleanup failure fixture. Make that a
shared mandatory assertion in those parameterized suites.

### SHOULD-6 - Platform durability policy: PASS

Unsupported advisory locks block; directory-fsync capability is recorded; file fsync/replace failures block and
preserve the last good bytes.

### Added risk - Dirty-content proof: FAIL

Content hashing now catches same-porcelain tracked/untracked mutation and binds index/content across S0/S1/S2,
resume, and cleanup. It still omits ignored files because `--untracked-files=all` does not enumerate them. The
preservation contract includes ignored user state; add a bounded no-follow ignored-path enumeration and fold its
type/content/link aggregate into `root_content_digest`, or explicitly narrow the product promise and justify why
ignored content may be unproved. Limit excess must remain blocking.

### Added risk - Removing recovery: FAIL

The `removing` state and exact present-versus-absent recovery shapes are correct. The remaining blocker is the fresh
authority/active-lease rule for the branch where recovery executes `git worktree remove` again, as recorded under
MUST-6. Finalization of an already absent exact registration/path must be distinguished from destructive retry.

### Added risk - Durable failure disposition: FAIL

Categories, retry limits, post-intent manual preservation, generic-retry suppression, and restart behavior are
frozen. The attempt record lacks its own exact storage path, per-field JSON types/caps/nullability, CAS revision,
canonical byte encoding, atomic replace/fsync protocol, and clock semantics for `retry_at`. State that it uses the
stable ownership root and the same locked durable-write protocol, define wall-clock rollback/invalid-time handling,
and bind admission to an exact successfully persisted record revision. Otherwise "persistence failure is manual"
cannot itself be durable.

### Added risk - `change_kind`: PASS

Accepted normalized issue types, exact `bug -> fix` mapping, all-other-accepted `-> feat`, review blocking for unknown
types, DTO binding, independent branch derivation, and drift tests now close this omission.

### Final gate for Build

**FAIL.** Remaining blockers: shared atomic pair lock/reread, fully exact runner argv, per-field wire schemas,
authorized removing retry, ignored-content proof, and durable attempt-record protocol. The three failed SHOULD test
amendments should be added in the same revision because they are the executable proof of those contracts.

## Amendment Recheck 2

Rechecked `Binding Amendment 2` against every remaining FAIL in the preceding recheck. The implementation contract is
nearly closed, but three contradictions/omissions still require one final textual amendment.

### Shared pair lock and pair-wide reread: PASS

Amendment K binds the loader to the exact `_ticket_lock_path` locks already used by `apply_route_resolutions`, fixes
the common case-folded acquisition order, performs a complete two-card reread before parsing, and creates the durable
digest only from the second stable pair. Its injected coordinator/child-set replacement tests also require zero Git
and zero generic fallback.

### Exact fetch argv and origin parser: FAIL

The production vector itself passes: complete ordered argv, forced refspec, exact `/dev/null`, POSIX capability
checks, one normalized origin, domain-separated origin digest, and a shared failure spy are all frozen. The remaining
contradiction is with the still-binding test contract: Amendment 1 says tests inject file transport and the frontier
uses temporary local Git remotes, while Amendment L's only accepted vector fixes `protocol.file.allow=never` and its
only accepted origin schemes are HTTPS/SSH. Those fixtures cannot exercise the real fetch path under the sole allowed
parser/vector.

Freeze one of two coherent choices: either run a bounded local HTTPS/SSH test remote with the exact production vector,
or define a separately named injection boundary that simulates fetch after asserting the production argv/origin
contract and explicitly state that no file origin reaches product parsing. Do not permit a test-only production argv
variant or silently relax the origin parser.

### Per-field manifest/proof/activation/ownership/attempt schemas: FAIL

Amendment M closes canonical bytes, nullability defaults, revisions, paths, digests, snapshots, proofs, activation,
per-child ownership/tombstone records, attempt records, discovery caps, case collisions, and golden byte fixtures.
One attempt field remains unmapped: `workflow_generation` is an exact key but is neither one of the named identity
fields nor a `*_digest`, fingerprint, source/catalog revision, transition identity, lease token, or run ID. Assign it
one exact JSON type/grammar—recommended: 64 lowercase hex for the immutable generation digest—and use that same rule
in admission and route-pair reset tests.

### Fresh authority for destructive removing retry: PASS

Amendment O explicitly requires a freshly verified authorization and the same active owning lease before a retry may
execute plain worktree removal. Expired/missing authority preserves. Already-absent exact artifacts permit proof-only
finalization and no Git mutation.

### Named crash fixtures: PASS

All six required seams are named with exact persisted/artifact shapes and restart outcomes: after fetch, prepared,
add, verification, removing intent, and physical removal.

### Generated manifest/property fixtures: PASS

Deterministic generated tests now cover unknown/missing keys, boundary lengths/counts, bool-as-int, Unicode aliases,
controls/surrogates, duplicate keys, randomized key order, and canonical byte round trips across every durable object.

### Command and fallback spy coverage: PASS

Amendment L makes the shared spy mandatory for every create, resume, prepared-recovery, removing-recovery, and cleanup
failure fixture; Amendment O also applies it at every named crash point.

### Ignored-content preservation: PASS

Amendment N adds the exact ignored status mode, bounded recursive no-follow hashing, type/link domain separation,
shared limits, administrative-path rejection, and equality checks across create/resume/cleanup snapshots.

### Durable attempt path, CAS, and clock: FAIL

The stable path, exact sidecar schema, manifest lock, canonical/atomic/fsync write protocol, revision CAS, nomination
capture, fatal persistence circuit breaker, injected UTC clock, rollback/forward behavior, and pre-Git attempt
increment all pass. The remaining contradiction is admission semantics:

- Amendment I says only `backoff` and `manual` candidates remain non-dispatchable and success marks `ready`, so a
  ready manifest/record must permit backend continuation and exact ready resume.
- Amendment P says both `manual` **and `ready`** records "never auto-admit," which can permanently suppress a ready
  child after restart or a later tick.

Freeze the distinction: `ready` must never schedule another provisioning retry, but it must admit the route-managed
worker through exact ready-resume/pre-backend attestation. `manual` never auto-admits; due `backoff` alone consumes a
new bounded provisioning attempt. Add a restart test proving a ready record resumes without fetch/add and reaches the
backend barrier exactly once.

### Contradictions introduced by Amendment 2

1. The only accepted HTTPS/SSH production origin/vector conflicts with the promised injected file-transport Git
   fixtures.
2. A `ready` disposition is success/dispatchable in Amendment I but grouped with never-admitted `manual` in P.
3. The supposedly total per-field table omits `workflow_generation`, although it is required in every attempt record.

No other contradiction was found in the amended lock order, manifest states, removal authority, ignored-state proof,
runtime generations, default-off behavior, or cleanup denial.

### Final gate for Build

**FAIL.** Before Build, freeze the test transport injection boundary, map `workflow_generation`, and clarify that
`ready` bypasses provisioning retry while remaining eligible for exact resume and backend admission. All other prior
MUST/SHOULD findings pass under Binding Amendments 1 and 2.

## Amendment Recheck 3

Rechecked `Binding Amendment 3` against the three remaining blockers in Amendment Recheck 2, including the final
cleanup-authorization scalar clarification.

### Test transport injection boundary: PASS

The product still parses only canonical HTTPS/SSH origins and requests the single byte-exact production fetch vector.
The injected fixture asserts that complete command, environment, and origin digest before simulating only the fixed
remote-tracking-ref movement. A file URL reaches neither product parsing nor the product runner; all other Git,
snapshot, worktree, registry, and content operations remain real against the temporary repository. This resolves the
file-fixture conflict without a test-only product argv or a relaxed origin policy.

### `workflow_generation` wire rule: PASS

The field is now required 64-lowercase-hex in both attempt and authorization objects and is derived by one
domain-separated SHA-256 over canonical validated safety-profile bytes. Admission, reload, route-pair scope reset,
and CAS use the same value and grammar.

### Ready admission semantics: PASS

The replacement rule separates provisioning retry from worker admission: manual never admits, non-due backoff does
not admit, due backoff consumes one persisted bounded attempt, and ready consumes no attempt but enters exact
ready-resume and pre-backend attestation. Missing/mismatched ready manifests become owned manual failures. The restart
fixture proves one resume, no fetch/add, and one backend barrier.

### Cleanup authorization scalar closure: PASS

The latest scalar clarification is binding and supersedes Amendment M's broader generic row where necessary:

- workflow-generation, transition-identity, and authorization digests are required 64-lowercase-hex strings;
- `issue_id` is the canonical managed child identifier;
- `run_id` uses the existing exact 32-lowercase-hex grammar and must equal the owning lease token;
- issuer is the one exact production issuer value;
- every authorization field is required and non-null.

These rules preserve equality with the active owning lease and remove string-width or optional-field ambiguity from
initial cleanup and destructive removing recovery.

### New contradiction check: PASS

No new contradiction remains. The fetch double changes fixture state only after validating the unchanged product
request; it does not introduce a second transport policy. The generation digest is consistent with immutable runtime
generations. Ready admission bypasses only provisioning retry—not route, manifest, lease, or pre-backend checks. The
32-hex run/lease rule is an explicit narrow override, not a competing interpretation of the earlier generic digest
row.

### Final gate for Build

**PASS.** Binding Amendments 1, 2, and 3 close every MUST, SHOULD, added-risk, and contradiction finding in this
plan attack. Frontier 003 may enter Build with temporary local fixtures only and no live Git/network mutation.
