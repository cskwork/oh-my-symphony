# Frontier 002/003 — immutable Git object trust

Date: 2026-07-20  
Mode: read-only design analysis. No network, fetch, branch, worktree, checkout, cleanup, secret inspection, or
product/test/Frontier 002 plan edit was performed.

## Decision

Route ownership evidence should be read from regular blobs in one immutable commit resolved from the fixed local
`refs/remotes/origin/aidt-prd` ref. Do not read marker or anchor bytes from the canonical service working tree, and do
not require that working tree or its index to be clean.

Keep the route field name `checkout_revision` to avoid widening the Frontier 003 handoff, but bind its meaning to the
full commit used for every marker/anchor read. Record the fixed revision source as
`refs/remotes/origin/aidt-prd`; include the changed trust semantics in the route fingerprint/schema version. Frontier
003 can then fetch `aidt-prd`, compare the fetched commit to `routing.checkout_revision`, and proceed only on exact
equality.

This is the smallest root-cause fix that satisfies both frontiers. It removes the canonical checkout's mutable files
from the routing trust boundary instead of asking the operator or provisioner to clean, stash, move, or reinterpret
user-owned state.

## Confirmed conflict

Frontier 002 currently requires all of the following at once:

- resolve `HEAD^{commit}`;
- prove every marker/scoring anchor is tracked;
- read those paths from the working tree;
- require `git status --porcelain=v1 --untracked-files=all` to be empty so `HEAD` pins the bytes.

Frontier 003 has the opposite lifecycle requirement: the canonical service checkout is user state, may be dirty, and
must retain its branch, index, tracked files, untracked files, and worktree registrations byte-for-byte while an
isolated feature worktree is provisioned.

The live read-only observation makes the conflict concrete. `aidt-viewer-api` has user-owned untracked state, while
both `HEAD` and the current local `origin/aidt-prd` resolve to
`84a3d1723f2ba35150fb56d400621d4f8cc261fb`. The current Frontier 002 cleanliness gate would reject this checkout even
though the immutable base commit and its ownership anchors are available and equal to the intended provisioning
base. Cleaning it would violate Frontier 003.

The in-progress Symphony checkout is early enough to correct the contract without migration churn. The current
uncommitted implementation materializes five files from the planned six-file boundary: Jira/source and file-batch
edits in three product files, new `src/symphony/aidt_routing.py`, and `tests/test_aidt_routing.py`;
`orchestrator/core.py` integration was not yet modified at the final read. The new module implements the conflicting
contract directly: `_observe_service` opens working-tree paths through `_read_anchor`, `_git_revision` resolves
`HEAD`, requires empty full porcelain status, and validates paths through `ls-files`. The test file names that old
`HEAD`/clean-tree rule and treats a working-tree marker/anchor symlink as a routing input. These are the precise seams
to amend; the Jira/source and route-card batch edits do not need redesign for this conflict. No `CLAUDE.md` exists in
the Symphony worktree; `AGENTS.md` and the repository's Symphony `MONOREPO` guidance were applied.

## Options considered

| Option | Safety and operational effect | Decision |
|---|---|---|
| Keep the whole-checkout clean gate | `HEAD` pins working-tree reads, but ordinary untracked notes/worktrees stop all routing and create pressure for destructive cleanup. It directly conflicts with Frontier 003. | Reject. |
| Allow untracked files, or compare only configured working-tree anchors with `HEAD` | Smaller textual amendment and enough for the current sample, but anchor reads remain exposed to filesystem races, tracked user edits need more exception logic, and `HEAD` can be `aidt-dev`/`aidt-stg`, causing the later `origin/aidt-prd` equality gate to fail. | Reject. |
| Read immutable objects at the canonical checkout's `HEAD` commit | Removes dirty working-tree bytes from trust and is safe for the current `viewer-api` sample. It still makes routing depend on whichever branch the operator has checked out; `lms-api` and `viewer-web` are already observed on non-production branches. | Safe fallback, not recommended. |
| Read immutable objects at fixed local `refs/remotes/origin/aidt-prd` | Ignores user working-tree state, routes from the same branch family Frontier 003 must fetch, and converts stale-local-ref risk into an explicit post-fetch equality check. No network is added to Frontier 002. | Recommend. |

## Recommended trust contract

### Repository and revision binding

For each enabled catalog service:

1. Validate the configured AIDT root and checkout exactly as closed catalog paths: absolute root, one relative
   checkout segment, no symlink in root/checkout path components, expected checkout directory, and no Jira-derived
   path/ref/argument.
2. Capture identity tokens for the AIDT root, checkout, checkout `.git` entry, resolved Git directory, and common Git
   directory. Reject symlinked Git metadata and any non-regular/non-directory shape not explicitly supported.
3. Run only bounded, sanitized, fixed-argv Git commands with `--no-optional-locks` and replace objects disabled.
   Assert `--show-toplevel` is the trusted checkout and object format is `sha1`.
4. Resolve exactly `refs/remotes/origin/aidt-prd^{commit}`. Accept one lowercase ASCII 40-hex line. An absent,
   malformed, non-commit, or changing ref is a global `git_invalid`/`revision_changed` failure.
5. Use that captured SHA, never the mutable ref or `HEAD`, in all subsequent object reads.

The Git environment must discard inherited `GIT_*` routing/index/object-directory overrides, disable replacement
objects, bound timeout/stdout/stderr, and never log raw output, checkout paths, object content, or exception strings.

### Marker and anchor reads

For each catalog marker or scoring anchor path:

1. Validate the relative path lexically; reject absolute paths, traversal, controls, empty components, and over-limit
   values.
2. Query the captured commit with a fixed `ls-tree -z --full-tree <sha> -- <path>` argv. Parse exactly one NUL
   record for the exact requested path.
3. Require mode `100644` or `100755`, type `blob`, and one lowercase 40-hex object ID. Reject tree, symlink
   (`120000`), submodule (`160000`), duplicate, missing, extra, truncated, or mismatched output.
4. For scoring anchors, read the raw blob with fixed `cat-file blob <blob-id>`, enforce the existing byte cap, decode
   strict UTF-8, and perform only the frozen literal matches. Marker presence requires the regular blob record but
   need not read its body.
5. Cache by `(commit SHA, path, blob ID)` within the observation so repeated categories cannot read divergent bytes.

Do not call `git status`, `git diff`, `ls-files`, `hash-object` on a working-tree path, `Path.read_text`, or
`open()` on a marker/anchor. Staged, unstaged, untracked, and ignored working-tree state is outside routing evidence.

### Symlink, TOCTOU, and fail-closed behavior

The trust boundary changes; it does not weaken:

- Root, checkout, `.git`, Git directory, and common-Git path symlinks remain hard failures because Git must traverse
  those filesystem paths.
- A symlink committed at a marker/anchor path is rejected by tree mode. A symlink at an intermediate committed path
  cannot resolve to an exact nested blob and therefore fails.
- A working-tree replacement or symlink at a configured anchor path is ignored because that path is never opened.
  Tests should prove it cannot affect evidence, score, revision, or fingerprint.
- Recheck root/checkout/Git identities, top level, object format, and the fixed ref-to-SHA binding after each service
  observation and again under the route batch locks immediately before the first rename. Any difference is global,
  writes nothing, and blocks dispatch.
- The commit/tree/blob hashes bind all scored bytes even if Git objects are concurrently pruned or the repository is
  swapped: missing objects fail; a different tree or blob cannot retain the same trusted object ID absent a hash
  collision. Identity/ref rechecks catch path/ref drift.
- A ref move after the final Frontier 002 preflight remains an explicit residual boundary. Frontier 003 must fetch,
  resolve the remote-tracking commit again, and require exact equality before any manifest or worktree mutation.

## Exact code amendments within the frozen six-file scope

### `src/symphony/aidt_routing.py` (new)

- Define a non-configurable revision source such as
  `_AIDT_BASE_REF = "refs/remotes/origin/aidt-prd"`; neither workflow/Jira/card text may override it in this frontier.
- Change `_default_git_runner`/`_git_read` to be binary-safe so `ls-tree -z` and blob bytes can be parsed without
  newline/text ambiguity. Keep a separate exact single-line decoder for scalar Git results.
- Implement a repository observation containing checkout/Git identity tokens, `revision_ref`, `checkout_revision`,
  and a map of trusted regular blob IDs/bytes.
- Replace `_git_revision`'s `HEAD` + `status` + `ls-files` flow with fixed-ref resolution, exact tree-entry parsing,
  and raw blob reads. Delete `_read_anchor` and stop adding marker/anchor paths to `_ObservedService.identities`;
  keep injectable identity, Git-runner, and precommit recheck seams for root/checkout/Git metadata.
- Change `_observe_service` to resolve the repository/base observation first, then populate
  `_ObservedService.contents` only from the captured commit's blobs. It must never call `_check_components` or
  `open()` for a catalog marker/anchor working-tree path.
- Compute context/code/domain evidence only from trusted blob bytes. Markers remain shape evidence and never score.
- Store the fixed ref and resolved commit in coordinator/child route metadata. Include the revision-source semantic
  version and sorted service-to-base commits in the route fingerprint.
- On absent ref, bad object shape/output, identity drift, ref drift, or blob failure, return only an allowlisted global
  error category and block candidate fetch before route writes.

### Existing five files

No trust-model change is needed in `trackers/jira.py`, `jira_intake.py`, `trackers/file.py`, or
`orchestrator/core.py` beyond their already planned Frontier 002 responsibilities. In particular, do not add cleanup,
fetch, worktree, or generic `Issue` behavior. `tests/test_aidt_routing.py` carries the required contract changes below.

## Exact test amendments

1. Replace `test_git_reader_binds_head_toplevel_sha1_clean_tracked_anchor_and_exact_output` with
   `test_git_object_reader_binds_fixed_aidt_prd_commit_regular_blobs_and_exact_output`:
   - create a temporary repository and local `refs/remotes/origin/aidt-prd` ref;
   - assert the exact fixed ref, `ls-tree -z`, and `cat-file blob` argv/output contract;
   - assert there is no `status`, `ls-files`, or working-tree file-read call;
   - reject extra lines/NUL records, wrong path/mode/type/OID, oversize output, invalid UTF-8, timeout, and nonzero
     exit.
2. Add `test_dirty_canonical_checkout_is_not_routing_input`: after freezing the base ref, add staged, unstaged,
   untracked, and ignored sentinels, including changed anchor bytes; route result, evidence, commit, and fingerprint
   must equal the clean observation, and every sentinel remains unchanged.
3. Split the current combined symlink test:
   - root/checkout/Git-metadata symlink ancestors fail globally;
   - committed marker/anchor symlink or non-blob tree entry fails globally;
   - a working-tree-only anchor symlink/replacement is not opened and cannot change the decision.
4. Amend `test_root_checkout_and_anchor_swap_during_read_fails_before_route_write` to inject root, checkout,
   Git-directory/common-directory, and fixed-ref drift. Anchor inode drift is removed because anchor filesystem paths
   are no longer observed. Each injected drift must preserve coordinator bytes and skip candidate fetch.
5. Add `test_route_revision_uses_aidt_prd_object_when_head_is_on_another_branch`: keep `HEAD` on a different commit,
   place valid ownership anchors in the fixed base commit, and assert routing uses the base commit. This prevents an
   operator's checked-out `aidt-dev`/`aidt-stg` branch from becoming a hidden provisioning prerequisite.
6. Add `test_base_ref_move_recomputes_or_blocks_before_write`: a ref move before batch preflight writes nothing; a
   stable later poll recomputes `checkout_revision`, marks prior route output stale under existing ownership rules,
   and produces a new fingerprint.
7. Keep the existing source, scoring, collision, partial-apply, preservation, health, and dispatch-barrier tests.
   Their behavior is independent of whether evidence bytes came from the worktree or an immutable tree.

Temporary Git fixture mutation is sufficient. No test may fetch the network or touch the real AIDT repositories.

## Exact Frontier 002 plan amendments

The product/test scope stays exactly six files. Amend the plan before Build as follows:

- **Binding amendment 3, filesystem observation:** root/checkout/Git metadata identities are the filesystem trust
  boundary. Replace anchor `O_NOFOLLOW`/`lstat`/`fstat` reads with exact committed tree-entry/blob validation and
  identity/ref rechecks. State explicitly that working-tree marker/anchor paths are never opened.
- **Binding amendment 4, Git observation:** replace `HEAD`, tracked-anchor index checks, and empty porcelain status
  with the fixed local `refs/remotes/origin/aidt-prd` commit plus exact `ls-tree -z`/`cat-file blob` semantics. Dirty
  canonical working trees are permitted and ignored by routing.
- **Binding amendment 7, fingerprints:** define each service revision as the fixed base-ref commit and include a new
  trust/schema tag so old `HEAD`-based fingerprints cannot compare equal.
- **Theory/Priority/Frozen configuration:** state that ownership is proved against the immutable AIDT production-base
  object available in the canonical repository. The base ref is fixed policy, not a configurable schema key.
- **Routing/Card contract:** define `checkout_revision` as the exact commit whose tree supplied every marker/anchor;
  optionally persist `checkout_ref` with the fixed value for auditability. It is not the current working-tree `HEAD`.
- **Accepted constraints:** remove “service base-ref validation does not belong here.” Frontier 002 validates only the
  local fixed ref/object; it still performs no fetch and no worktree creation. Frontier 003 retains fresh-fetch and
  equality authorization.
- **Required tests:** replace the old clean-HEAD test and split the old filesystem-anchor symlink/TOCTOU tests using
  the cases listed above. Add dirty-root preservation and non-production-HEAD/base-object proof.

Frontier 002's pre-Frontier-003 dispatch barrier remains unchanged. Object-backed routing decides ownership and
records the candidate base; it does not authorize a worker until Frontier 003 has fetched and matched that base.

## Migration and Frontier 003 impact

- Runtime migration: none. Routing is default-off and no active profile contains `aidt_routing`.
- Configuration migration: none if the base ref remains fixed policy; the closed catalog schema does not gain a
  user-controlled ref.
- Route artifacts/fingerprints: bump the route trust/schema tag. Any prototype `HEAD`-based card must be treated as
  stale and recomputed, never reinterpreted in place. The current uncommitted implementation has not produced an
  activated durable format.
- Frontier 003 input: it can continue consuming `routing.checkout_revision`. Before mutation it verifies repository
  identity and object existence, fetches only `aidt-prd`, resolves the fetched remote-tracking commit, and requires
  equality. A mismatch is `route_base_mismatch`, preserves all state, and requests rerouting.
- Convergence after remote movement: the Frontier 003 fetch updates the local remote-tracking ref; the next routing
  poll observes the new immutable commit and emits a new route fingerprint. Provisioning remains blocked until a
  subsequent fresh-fetch equality check passes.
- User state: no cleaning, stashing, checkout, reset, index rewrite, branch movement, or untracked-file handling is
  introduced in either frontier.

## Residual boundary and non-goals

The local remote-tracking ref can be stale until Frontier 003 fetches; this is acceptable because Frontier 002 blocks
routed dispatch and Frontier 003 requires fresh equality. The resolver must surface a bounded recheck category, not
silently substitute `HEAD`, `FETCH_HEAD`, `aidt-dev`, or `aidt-stg`.

This amendment does not authorize network access, change branch naming, create/remove worktrees, alter generic
workspace cleanup, enable auto-commit/auto-merge, or inspect AIDT product files from the working tree. Those remain
Frontier 003+ responsibilities.
