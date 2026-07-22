# Persisted recovery-proof API

## Decision

Frontier 003 can prove every required restart shape with the existing `aidt-worktree-v1` snapshot schema. No schema
amendment or migration is required. The aggregate ref and registry digests are not subtractable, but subtraction is
unnecessary: Git state can rebuild the canonical digest input by replacing the current target record with the fully
known persisted target record, then recompute the existing domain-separated digest.

This is a narrow restart API. Uninterrupted create and remove continue to pass the genuine before and after
`RepositoryState` values to `validate_create_delta` and `validate_remove_delta`. The provisioner must delete
`_state_from_snapshot`; it never constructs a `RepositoryState`, reads its raw tuples, calls a private Git-state
helper, or probes the target path itself.

## Public surface

Add these frozen DTOs and functions to `git_state.py` and the lazy `symphony.aidt_worktree` facade:

```python
@dataclass(frozen=True)
class PreparedRecoveryProof:
    state: RepositoryState
    ticket: TicketWorktreeState | None
    create_delta_digest: str | None

@dataclass(frozen=True)
class ReadyRecoveryProof:
    state: RepositoryState
    ticket: TicketWorktreeState

@dataclass(frozen=True)
class RemovedRecoveryProof:
    state: RepositoryState
    remove_delta_digest: str

def prove_prepared_recovery(
    identity: RepositoryIdentity,
    persisted_s1: RepositorySnapshot,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    runner: BinaryRunner | None = None,
) -> PreparedRecoveryProof: ...

def prove_ready_recovery(
    identity: RepositoryIdentity,
    persisted_s2: RepositorySnapshot,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    phase: Literal["resume", "cleanup_pre"],
    runner: BinaryRunner | None = None,
) -> ReadyRecoveryProof: ...

def prove_removed_recovery(
    identity: RepositoryIdentity,
    persisted_cleanup_pre: RepositorySnapshot,
    retained_branch_sha: str,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    runner: BinaryRunner | None = None,
) -> RemovedRecoveryProof: ...
```

Each result contains a genuinely observed current `RepositoryState`; no raw collection is copied from a persisted
snapshot. The provisioner may persist `state.snapshot` and pass `state` opaquely to an existing delta validator, but
must not inspect or rebuild `state.refs` or `state.registrations`. `PreparedRecoveryProof` permits only
`(s1, None, None)` or `(s2, TicketWorktreeState, digest)`. The exact form requires a clean ticket at the base with no
upstream. Ready proof requires its state phase to equal the requested phase and its ticket fields to match that state.
Removed proof requires `cleanup_post`, a retained target ref, no target registration, and the standard remove-delta
digest.

The three functions are read-only. They run only bounded observation commands and internal no-follow path checks. A
target collision or mixed shape raises; there is deliberately no successful `AMBIGUOUS` proof. The caller converts
such permanent failures to owned-preserved/manual state without Git mutation.

## Shared preconditions and equality

The caller holds the common-Git lock followed by the manifest lock, re-reads the manifest/attempt record, and
re-attests route, scope, binding, and manifest identity before calling. `current_binding_digest` is the result of that
fresh binding observation, not a value copied from the manifest. Each function requires it to equal the persisted
snapshot and re-observes `RepositoryIdentity` exactly as `observe_repository_state` does.

Within each persisted/projected comparison below, only `observed_at` is unobserved; phase and permitted target
movement are explicitly projected rather than silently ignored. The following remain exact in every proof:

- repository-binding digest;
- root HEAD and symbolic, status, content digest, content count, and content bytes;
- protected occupancy digest and count;
- fixed `refs/remotes/origin/aidt-prd` SHA;
- unrelated refs and registrations, including counts;
- target branch grammar and exact absolute workspace path.

The root content proof remains the Amendment N proof: ignored paths are included, ignored directories are traversed
without following links, and the existing 10,000-item, 512 MiB, and path-size caps apply. Dirty ticket files are not
root drift because a registered ticket worktree is excluded from the service-root proof.

Route, scope, attempt revision, lease, and authorization are not Git facts. The provisioner proves them under the same
locks. In particular, absent-target finalization passes the `cleanup_pre` snapshot and `retained_branch_sha` from the
locked partial `RemovalProof`; it retains that object's exact `authority_digest` when installing the returned post
snapshot and delta by manifest CAS. `prove_removed_recovery` does not pretend that an arbitrary digest is authority.

`observed_at` comes from the provisioner's injected whole-second UTC clock and is used only for the returned snapshot.
The proof API does not read a clock, change attempt counters, or schedule retries. Before entry, admission has already
consumed the exact attempt-record revision; the caller preserves its CAS discipline through the manifest transition.

## Canonical target and digest projection

For target ref name `n = "refs/heads/" + branch`, persisted SHA `h`, and exact manifest path `p`, the only admissible
persisted target records are:

```python
RefRecord(name=n, sha=h, upstream=None)
WorktreeRegistration(
    path=p,
    head=h,
    branch=n,
    detached=False,
    locked=False,
    prunable=False,
)
```

Before using either record, recompute its target-registration digest and require exact equality with the persisted
`target_registration_digest`. Refs and registrations are parsed with the existing uniqueness and 2,500-entry caps.
A registration is target-related when `path == p or branch == n`; exactly one matching both is required when present.
Any second candidate, path/branch mismatch, detached/locked/prunable flag, target upstream, or remote ref ending in
`/<branch>` is a collision before projection.

The projection is a reconstruction of the canonical hash input, not a reconstruction of a historical
`RepositoryState`:

- Persisted S1 has no target. For current absence, hash all current refs/registrations. For exact current S2, remove
  the one current target ref/registration and hash the remaining collections. Compare those hashes and adjusted
  counts with the full S1 hashes and counts.
- Persisted S2 includes canonical target records at creation SHA `h0`. For current ready state at `h1`, remove the
  exact current target records, insert the canonical persisted records at `h0`, sort using the existing digest
  ordering, and compare the resulting full hashes and counts with S2. Separately require ref, registration, and ticket
  HEAD all equal `h1`.
- Persisted cleanup-pre includes the target registration. After physical removal, require the exact local branch at
  `retained_branch_sha`, no registration candidate, and `lstat` absence. Insert the canonical persisted registration
  into the current registry before hashing. The retained ref remains in place, so the reconstructed refs collection
  must equal cleanup-pre directly.

This is sound because the persisted target payload is fully determined by branch, path, SHA, and the required false
flags/no-upstream rule. The domain-separated SHA-256 digest covers the sorted canonical JSON for the complete
collection; counts independently bind cardinality. A changed, missing, or added unrelated record therefore changes
the reconstructed preimage and fails, subject only to the ordinary SHA-256 collision assumption. No algebraic digest
removal, raw historical tuple, synthetic state, or new durable field is involved.

## Operation semantics

### Prepared recovery

`persisted_s1.phase` must be exactly `s1` with both target fields null.

- If target ref, target registration candidate, and target path are all absent, observe a current S1 and require full
  persisted equality. Return the absent-shaped proof. The caller may run one exact add.
- If one complete target exists, require local ref/registration/ticket HEAD at the persisted base SHA, exact path and
  branch, no upstream, clean ticket, allowed flags, exact root/fixed/protected state, and S1 unrelated projections.
  Return a genuine current S2 plus the normal `aidt-create-delta-v1` digest. The caller performs no add and may persist
  ready. In the absent case, the caller carries the returned genuine S1 through add and pairs it with the genuine S2
  in `validate_create_delta`.
- Branch-only, path-only (including file, directory, symlink, or broken symlink), registration-only, remote-feature,
  dirty, wrong-SHA, upstream, detached, locked, prunable, mixed, or unrelated-delta states never return a proof.

### Ready resume and cleanup entry

`persisted_s2.phase` must be exactly `s2` with canonical non-null target fields. Both modes require one complete current
target; exact path/branch; ref = registration = ticket HEAD; no upstream; fixed base unchanged; persisted base is an
ancestor of current HEAD; and root, protected, and unrelated projections equal S2.

`phase="resume"` permits clean or dirty ticket status and permits HEAD to stay at S2 or advance to any descendant.
It returns a genuine `resume` state and performs no fetch, add, remove, checkout, reset, or rebase.

`phase="cleanup_pre"` adds `ticket.clean is True` and performs the ancestry and equality checks immediately before
the caller persists `removing`. It returns a genuine `cleanup_pre` state. The caller carries that exact state through
plain remove and pairs it with a genuine observed cleanup-post state in `validate_remove_delta`.

### Branch-retained removal finalization

`persisted_cleanup_pre.phase` must be exactly `cleanup_pre`, and `retained_branch_sha` must equal its target ref SHA.
Success requires exactly the retained local ref at that SHA with no upstream, the fixed ref/root/protected state
unchanged, no target registration candidate, and no filesystem entry at the target path. Registry reconstruction and
full ref equality prove all unrelated state unchanged. The function returns a genuine current `cleanup_post` state
and the normal `aidt-remove-delta-v1` digest; it executes no Git mutation.

This function is the sole narrow exception for the branch-retained shape after an authorized removing intent. The
same branch-only shape in prepared recovery, without an exact partial `RemovalProof`, with a missing/moved/upstream
branch, with an existing path, or with any registration/ref drift remains a collision. If the exact registration and
path still exist, this proof also fails: only the separately reauthorized plain-remove retry may handle that state.

## Failure categories

| Category | Exact use |
|---|---|
| `protocol_invalid` | wrong DTO type/phase, invalid recovery phase, inconsistent nullable proof shape, malformed bounded Git output |
| `binding_invalid` | freshly supplied binding differs from persisted binding |
| `identity_invalid` | repository identity, root HEAD/symbolic, protected occupancy, or reconstructed unrelated refs/registrations differ |
| `content_invalid` | root status/content proof differs, or prepared/cleanup requires a clean ticket and it is dirty |
| `base_invalid` | fixed base differs, retained SHA conflicts with cleanup-pre, or current ready HEAD is not descended from the persisted base |
| `collision` | any target/remote-feature/path/registration mixed shape, wrong target HEAD, upstream, detached, locked, or prunable state |
| `branch_invalid` / `path_invalid` | invalid input branch/path or an indeterminate no-follow path observation |
| existing command/cap categories | timeout, overflow, entry cap, parser, identity preflight, or local command failure; no remapping or fallback |

Every category is fail-closed. None permits a mutation from inside these functions. The provisioner preserves every
failure except its already-frozen bounded retry policy; recovery-proof failures are post-intent and therefore manual.

## Implementation constraints

Each new public function and each new helper is at most 50 physical lines with nesting at most four. Keep observation,
canonical-target construction, collection projection, snapshot-invariant comparison, and result-shape validation as
separate cohesive helpers. Private digest and path helpers remain internal to `git_state.py`; only the three public
operations and their DTOs enter the facade. Do not expose a generic “digest this caller-provided collection” API.

The functions use one runner, existing bounded Git argv/environment, and no mutation commands. The caller-held locks
are preconditions, not reimplemented here. A returned proof is consumed before releasing those locks and is never
cached as current truth beyond the manifest transition it authorizes.

## Exhaustive temporary-Git tests

Use only temporary SHA-1 repositories with a canonical HTTPS fixture origin and the injected runner. The command spy
must reject network and assert that all three proof functions issue zero fetch/add/remove/checkout/reset/rebase/prune
commands.

1. Prepared: accept exact S1 absence and exact clean base S2 after both named add crash points. Reject independently
   branch-only, path-only regular/dir/symlink/broken-symlink, registration-only, remote-feature, dirty tracked,
   untracked and ignored content, wrong SHA, upstream, detached, locked, prunable, duplicate/mismatched candidate,
   fixed-ref drift, root drift, protected drift, unrelated-ref drift, and unrelated-registration drift.
2. Ready resume: accept unchanged clean, dirty tracked/untracked/ignored, one descendant commit, and dirty descendant.
   Reject root HEAD/symbolic/status/content changes, same-status ignored-content mutation, fixed/protected/unrelated
   drift, target path/branch/upstream/flag mismatch, ref-registration-ticket HEAD mismatch, remote-feature collision,
   and force-moved non-descendant HEAD.
3. Cleanup entry: accept clean base and clean descendant; repeat every ready rejection and additionally reject every
   dirty ticket shape. Assert the ancestry observation occurs before the returned cleanup-pre proof is persisted by
   the provisioner fixture.
4. Removed recovery: after physical remove but before removed fsync, accept only retained exact branch plus absent
   path/registration, return the canonical cleanup-post/remove digest, and finalize proof-only. After removing fsync
   but before physical remove, reject this proof because the exact registration remains; test the separate fresh-
   authority plain-remove retry with a genuine before/after pair. Also reject branch absent, wrong/moved/upstream
   branch, path-only artifacts of every no-follow type, registration-only/mismatched registration, remote-feature
   collision, fixed/root/protected/unrelated drift, retained-SHA mismatch, and incomplete or already-complete
   persisted snapshot phases.
5. Projection properties: generate bounded deterministic unrelated ref/registration sets and prove add/delete/change/
   rename of any unrelated record fails while replacing only the target with its persisted canonical record passes.
   Tamper each digest/count/target field independently, including boundary and boundary-plus-one counts.
6. API and safety: assert result DTO shape invariants, lazy facade exports, runner error propagation, parser/cap failures,
   observed-time-only tolerance, no provisioner import of underscored Git helpers, no direct provisioner `lstat`/
   `exists`/`is_dir` probe, no `_state_from_snapshot`, and genuine uninterrupted before/after validator pairs.

No live repository, network, Jira, AIDT checkout, product file, durable fixture, or schema golden byte is changed by
this design.
