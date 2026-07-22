# Frontier 003 provisioner draft attack report

Date: 2026-07-21

## Decision

**FAIL.** The draft has the intended lifecycle outline, exact mutation primitives, deny-all cleanup default, and all six
named fault-hook calls, but it is not safe to export or integrate. Twelve MUST findings block Build: persisted Git
proof is reconstructed unsoundly, cleanup cannot durably complete, ready/removing proofs omit required state, route and
binding barriers are stale, durable sidecars have unrecoverable partial-write windows, and the tests are non-executable
tracers.

This was a read-only product/test attack. No product or test file was edited, and no live repository, network, Jira,
AIDT checkout, acceptance run, or commit was used.

## MUST findings

### MUST-1 - The frozen provisioner surface is not publicly importable

**Evidence:** `src/symphony/aidt_worktree/__init__.py:192-287` omits every provisioner type from `__all__`, and
`src/symphony/aidt_worktree/__init__.py:290-299` has no lazy provisioner export branch. Consequently
`tests/test_aidt_worktree_provisioner.py:7-14` fails during collection on `ActiveCompletionLease` before any test can
run.

**Risk:** Runtime/core cannot consume the frozen DTOs through the public facade, while eager imports would violate the
never-enabled lazy-import guarantee.

**Required repair:** Add a `TYPE_CHECKING` provisioner import set, a closed `_PROVISIONER_EXPORTS`, the seven frozen
public names to `__all__`, and one lazy `__getattr__` branch. Do not import `provisioner.py` or `git_state.py` during a
plain disabled facade import.

**Required test:** Prove every frozen name imports from `symphony.aidt_worktree`, unknown names still fail, import
permutations are cycle-free, and a never-enabled facade import leaves both provisioner and Git-state absent from
`sys.modules`.

### MUST-2 - Persisted snapshots are converted into false `RepositoryState` values

**Evidence:** `src/symphony/aidt_worktree/provisioner.py:362`, `:538`, and `:558` pass a fabricated before-state to the
delta validators. `_state_from_snapshot` at `src/symphony/aidt_worktree/provisioner.py:899-910` combines the persisted
before snapshot with the **after** observation's refs and registrations. This is not a state that ever existed.

For create, the identical raw tuples on both sides hide unrelated ref/registration changes. For removal, the fabricated
cleanup-pre state normally lacks the just-removed target registration, so `validate_remove_delta` cannot find the
removed target and successful physical cleanup cannot reach `removed`.

**Risk:** Recovery can accept an unproved create delta, while authorized cleanup can remove the worktree and then fail
permanently before the removed manifest/tombstone is durable.

**Required repair:** Delete `_state_from_snapshot`. Carry the real S1 `RepositoryState` through uninterrupted create
and the real cleanup-pre state through uninterrupted remove. Restart recovery needs a narrow public Git-state proof
API that validates a persisted snapshot against a current observation without inventing raw collections. If that API
cannot be added under the frozen foundation boundary, stop Build and amend the contract; do not reach into private Git
helpers or add filesystem inference.

**Required test:** Mutate one unrelated ref and one unrelated registration between persisted prepared/removing state
and recovery and require preservation. Separately prove a real S1 -> S2 create and cleanup-pre -> cleanup-post remove
reach `ready` and `removed` using genuine state pairs.

### MUST-3 - Branch-retained removal recovery bypasses the authoritative classifier

**Evidence:** `src/symphony/aidt_worktree/provisioner.py:508-522` treats `EXACT` as destructive retry, but sends
`AMBIGUOUS` to `_finalize_already_removed`; `ABSENT` always becomes `collision`. `_finalize_already_removed` at
`src/symphony/aidt_worktree/provisioner.py:541-560` then manually reclassifies ambiguity using only retained branch SHA
and null target registration. It does not prove path absence and, because of MUST-2, does not genuinely prove the fixed
ref or unrelated refs/registrations. An unregistered existing directory, remote feature ref, or other mixed artifact
can enter this fallback.

**Risk:** The draft either cannot recover the required branch-retained post-remove crash or can mark an ambiguous mixed
shape removed. Both violate Amendment O.

**Required repair:** Preserve every ordinary `AMBIGUOUS` result. Add one explicitly authorized public proof operation
for a persisted `removing` intent that distinguishes exact branch-retained/path-and-registration-absent state and proves
the recorded authority digest, retained branch SHA, fixed ref, root, protected occupancy, and all unrelated refs and
registrations. It must use no Git mutation and no direct path probe in the provisioner.

**Required test:** Cover branch retained plus path/registration absent as proof-only finalization, and parameterize
branch-only, path-only, registration-only, remote-feature-ref, locked, prunable, wrong-SHA, and unrelated-delta shapes
as preserved with zero mutation.

### MUST-4 - Ready resume and cleanup do not prove exact target or unrelated state

**Evidence:** `_require_ready_root` at `src/symphony/aidt_worktree/provisioner.py:884-896` compares root/protected fields
but omits `registry_digest`, `registry_count`, `refs_digest`, and `refs_count`; it only requires non-null target ref and
registration. `_resume_ready` at `src/symphony/aidt_worktree/provisioner.py:407-425` never proves the observed target
ref/registration matches the ticket HEAD and exact path. `_begin_removal` at
`src/symphony/aidt_worktree/provisioner.py:463-493` likewise does not require the ticket HEAD to equal the observed
branch/registration or prove base ancestry immediately before removal.

**Risk:** Dirty ticket work and descendant commits should be allowed, but unrelated ref/registration drift, a swapped
registration, or a force-moved non-descendant branch can currently pass enough checks to resume or begin deletion.

**Required repair:** Use one public ready/resume projection proof that allows ticket status/content and descendant HEAD
movement while requiring exact path/branch, matching target ref-registration-ticket HEAD, no upstream, base ancestry,
fixed ref, unchanged root/protected state, and unchanged unrelated refs/registrations. Cleanup must run the stricter
clean form immediately before persisting `removing`.

**Required test:** Prove dirty ticket changes and descendant commits resume; independently mutate root, protected
occupancy, unrelated refs, unrelated registrations, target path/branch/upstream, and force-move HEAD outside the base,
and require no backend admission or remove.

### MUST-5 - Before-run and cleanup route/binding checks are outside the protected barrier

**Evidence:** `attest_before_run` loads the route before acquiring locks at
`src/symphony/aidt_worktree/provisioner.py:220-223` and never reloads it inside the lock at `:224-230`. Cleanup reads the
manifest and route before locking at `src/symphony/aidt_worktree/provisioner.py:434-447`, then re-reads only the manifest
at `:448-451`. Neither initial cleanup nor removing recovery calls `_verify_binding`; `_observe` at
`src/symphony/aidt_worktree/provisioner.py:636-651` merely copies the expected digest into a snapshot.

**Risk:** A pair or repository-binding drift between the pre-lock read and backend/removal barrier can act under stale
authority. The common-Git lock does not serialize external route writers.

**Required repair:** After acquiring common-Git then manifest locks, reload and compare the exact route pair and call
the shared binding observer for before-run, initial cleanup, destructive removing retry, and proof-only finalization.
Keep the second immediate pre-mutation binding check where external Git assumptions can change.

**Required test:** Inject route-pair and binding replacement between pre-lock and locked reads for before-run, ready
cleanup, removing retry, and absent finalization; each must preserve/fail before backend, remove, or removed persistence.

### MUST-6 - Cleanup accepts the wrong ready-manifest authorization revision

**Evidence:** `src/symphony/aidt_worktree/provisioner.py:593-601` accepts
`authorization.ready_manifest_revision in {2, manifest.manifest_revision}`. For a revision-3 `removing` manifest this
admits a token claiming ready revision 3, even though the field is the frozen ready-manifest revision and the only ready
revision is 2.

**Risk:** A verifier returning true can authorize a destructive retry with a token bound to the wrong lifecycle state.

**Required repair:** Require exact ready revision 2 for both initial cleanup and every destructive retry, plus current
workflow generation/route pair, byte-equal recorded authority digest on retry, and the same active non-competing lease.
Proof-only already-removed finalization must use only the recorded intent and current non-mutating proof.

**Required test:** Even with an injected authority that returns true, deny ready revision 1, removing revision 3, wrong
generation/pair/digest, expired/inactive/missing/competing/different lease, issue/run/token/attempt mismatch; accept only
the exact revision-2 authority and lease.

### MUST-7 - Failure persistence can poison a newer attempt under a fabricated lock

**Evidence:** `prepare` releases its lifecycle locks before calling `_persist_failure` at
`src/symphony/aidt_worktree/provisioner.py:207-214`. `_persist_failure` then invents
`common-git-000...lock` at `src/symphony/aidt_worktree/provisioner.py:818-823`, reads whatever attempt is current, and
writes a failure at `:824-830` without requiring the admission identifier, route pair, workflow generation, owned
revision lineage, or non-ready disposition. A delayed old failure can therefore turn a newer scope or successful ready
attempt into manual state.

**Risk:** Durable CAS protects only the final reread revision; it does not prove the failing call owns that revision.
The fake common lock also violates the non-reversible common-Git lock contract and synchronizes with no mutation path.

**Required repair:** Persist under the real resolved common-Git lock when one was attested, otherwise under the manifest
lock only. Carry an exact failure context and update only the same identifier/pair/generation and expected phase/revision
lineage. Never downgrade a ready record or a record advanced by another owner. A storage failure must still surface as
`persistence_failed` for the runtime fatal circuit.

**Required test:** Pause one failing admission while another reaches ready and while another resets scope; release the
old failure and assert both newer records remain byte-identical. Assert no zero/fabricated common lock is opened and
that pre-intent retryable versus post-intent manual ordering remains exact.

### MUST-8 - Multi-file durable transitions have no partial-write recovery

**Evidence:** Prepared is written as manifest -> ownership -> attempt at
`src/symphony/aidt_worktree/provisioner.py:288-293`; ready as manifest -> ownership -> attempt at `:389-405`; removing
as manifest -> ownership -> attempt at `:482-492`; removed as manifest -> tombstone at `:573-582`. Recovery validates
neither exact sidecar alignment nor safely completes a partial transition. In particular, ready is persisted before
`read_optional_ownership` at `:392-395`, and removed cleanup returns immediately before locking or repairing ownership
at `src/symphony/aidt_worktree/provisioner.py:435-441`.

**Risk:** A crash/fsync failure between any two files leaves valid but inconsistent durable records: prepared with
phase `none`, ready with old ownership/attempt, removing with a ready attempt, or removed without a tombstone. Current
re-entry either mutates Git from an unowned state, converts it to manual, or reports handled without restoring final
ownership.

**Required repair:** Define an explicit recoverable write order for every manifest/ownership/attempt revision and, under
the same manifest lock, validate or finish only the exact permitted partial predecessor before any Git/backend action.
Unknown, skipped, or conflicting revisions preserve as owned error. Removed re-entry must validate/repair the exact
tombstone before returning handled.

**Required test:** Inject failure after every individual manifest, ownership, and attempt/tombstone persistence; restart
from each byte-exact partial shape and prove convergence or permanent preservation with no duplicate fetch/add/remove.

### MUST-9 - Admission and before-run guards are not compared as exact sealed DTOs

**Evidence:** `_attempt_for_admission` at `src/symphony/aidt_worktree/provisioner.py:662-674` omits
`attempt.identifier == admission.identifier` and maps every non-ready disposition, including manual/non-due backoff,
to `provision`. `attest_before_run` at `src/symphony/aidt_worktree/provisioner.py:216-230` ignores
`guard.workspace_path` and compares only attempt revision; `_resume_ready` at `:407-416` does not compare attempt
identifier, route pair, or workflow generation. `PreparedAidtWorktree` at
`src/symphony/aidt_worktree/provisioner.py:125-128` has no exact-type invariant.

**Risk:** A forged or mispaired same-revision record/guard can cross the final backend barrier, and a manual record can
be presented as a provisioning admission.

**Required repair:** Compare every admission/guard field against the exact stable path, manifest, attempt, current
settings, and route. Require action-specific attempt disposition/phase shapes; manual and non-due states never enter
prepare. Validate `PreparedAidtWorktree` contains exact result/guard types.

**Required test:** Swap same-revision attempts between children/scopes, forge action and workspace path, and exercise
manual/non-due/ready phase mismatches; every case must fail before Git or backend. Prove the returned guard alone passes
both initial and later-turn attestations.

### MUST-10 - The named crash hooks are swallowed as ordinary failures

**Evidence:** All four prepare hooks at `src/symphony/aidt_worktree/provisioner.py:285`, `:293`, `:333`, and `:369` run
inside the broad `except Exception` wrapper at `:207-214`. A normal injected exception is converted to a failure record;
post-intent hooks become manual and even the post-fetch/prepared hook becomes manual for `internal_error`. The documented
restart paths therefore cannot be exercised through `prepare`. Cleanup hooks at `:492` and `:536` are likewise
normalized by `cleanup` at `:232-246` rather than having a defined crash signal.

**Risk:** The six binding restart fixtures either must call private methods/use an accidental `BaseException`, or they
observe durable attempt mutations that a process crash would not perform.

**Required repair:** Define one bounded test-only crash sentinel contract that bypasses ordinary failure persistence and
sanitization while all real `Exception` failures keep current fail-closed handling. The six fixed seam names remain the
only accepted hook values.

**Required test:** Trigger every seam through the public method, reconstruct a new provisioner/runtime, and assert the
exact Amendment O restart shape and command sequence without an extra failure-record write.

### MUST-11 - The provisioner tests are names, not behavioral evidence

**Evidence:** `tests/test_aidt_worktree_provisioner.py:17-72` contains only method-existence assertions, two DTO field
assertions, and a deny-all call with protocol arguments suppressed by `type: ignore`. It creates no repository, durable
record, drift, recovery, authority, fault, or command/fallback witness. Collection currently fails before these tracers
run because of MUST-1.

**Risk:** Every unsafe transition above can appear green after exports are added.

**Required repair:** Replace all thirteen skeletons with the exact temporary-Git tests in `provisioner-test-brief.md`,
using the production-vector fetch double, real public Git observations/mutations, durable byte assertions, route/binding
drift injection, deterministic UTC clock, injected authority/lease, and the shared forbidden-command/generic-fallback
spy. Add all six named crash restarts, not merely hook-call assertions.

**Required test:** The replacement suite itself is the test requirement; each name must assert event order, durable
revisions, result/guard fields, created-now semantics, preservation, and forbidden side-effect absence.

### MUST-12 - The draft fails the static type gate at its internal public-API boundary

**Evidence:** `_identity_and_paths` returns `tuple[RepositoryIdentity, object]` at
`src/symphony/aidt_worktree/provisioner.py:609`, and most transition helpers accept `paths: object`; callers then access
`manifest_lock`, `manifest`, and `attempt` at `:222-225` and `:252-256`. Pyright reports eight product errors. The
runtime import/type workaround `_stable_paths` at `:941-947` does not narrow those earlier accesses.

**Risk:** The repaired `StableWorktreePaths` contract is discarded at the orchestration boundary, making path mix-ups
invisible to static verification.

**Required repair:** Import and use `StableWorktreePaths` in every relevant signature, remove the dynamic
`_stable_paths` workaround, and keep `StableMetadataPaths`/worktree paths distinct.

**Required test:** Pyright must report zero errors for the provisioner and behavioral test file; add a small type-level
fixture or constructor assertion if a path-family mix-up is otherwise not executable.

## SHOULD findings

### SHOULD-1 - Consume exact public return values instead of discarding/falling back

`_fetch` discards `FetchResult` at `src/symphony/aidt_worktree/provisioner.py:618-626`, although the frozen contract says
its SHA and binding digest are the post-fetch evidence. `_persist_ready` uses
`s2.target_registration_digest or ""` at `:386-388`, turning an impossible missing proof into a generic fallback before
the manifest validator rejects it.

Return and compare the exact `FetchResult` after pair re-attestation, and explicitly raise a bounded invariant failure
when registration proof is absent. Test a mismatched injected result/proof and require no prepared/ready persistence.

### SHOULD-2 - Prevent DTO representations from exposing paths and lease tokens

The default dataclass reprs at `src/symphony/aidt_worktree/provisioner.py:83-151` expose guard workspace paths and the
active lease run ID. These objects will cross runtime/core boundaries where diagnostic logging is likely.

Use `repr=False` plus bounded allowlisted reprs, or prohibit these DTOs from log payloads at the runtime boundary. Test
that hostile path/token sentinels never appear in repr, health, or logged exception detail.

### SHOULD-3 - Use keyword construction for durable proof records

The 30-field manifest constructor at `src/symphony/aidt_worktree/provisioner.py:687-697`, ownership constructor at
`:701-714`, and positional `PostProof`/`RemovalProof` constructors at `:386-388` and `:479-481` depend on field order.
All values currently line up, but a schema-preserving field review is difficult and a later appended field can be
silently misbound.

Construct durable DTOs with field keywords and keep the transition-local grouping visible. Golden canonical-byte tests
must remain unchanged.

## NIT findings

### NIT-1 - Remove redundant path narrowing after the type repair

Most methods call `_stable_paths` even when the value originated directly from `stable_worktree_paths`. Once MUST-12
uses the exact type, these repeated calls obscure the transition sequence without adding runtime safety. Keep validation
at the public path constructor and use the typed value directly.

## Positive observations

- The frozen public DTO field order and constructor surface are present at
  `src/symphony/aidt_worktree/provisioner.py:83-205`; admission, guard, and active-lease scalar validation is mostly
  closed and sanitized.
- Fresh create holds common-Git then manifest locks across classification, S0/fetch/S1, prepared intent, add, S2, and
  ready persistence at `src/symphony/aidt_worktree/provisioner.py:248-294`.
- The second binding check immediately before `add_worktree` is present at
  `src/symphony/aidt_worktree/provisioner.py:318-333`.
- Cleanup defaults to `DenyAllCompletionAuthority`, uses only plain public `remove_worktree`, and contains no force,
  prune, raw deletion, reset, checkout, rebase, or branch-delete fallback.
- All six Amendment O hook names are spelled exactly and placed at the intended high-level mutation boundaries.
- AST inspection found no function longer than 50 lines and no control-flow nesting deeper than four. The file remains
  cohesive around one lifecycle, though the proof helpers should stay separated conceptually from persistence and
  authorization checks.

## Read-only diagnostic evidence

```text
rtk ../../.venv/bin/ruff check --no-cache \
  src/symphony/aidt_worktree/provisioner.py tests/test_aidt_worktree_provisioner.py
```

Result: `All checks passed!`

```text
rtk ../../.venv/bin/pyright \
  src/symphony/aidt_worktree/provisioner.py tests/test_aidt_worktree_provisioner.py
```

Result: `21 errors` (8 product path-type errors; 13 test errors cascading from missing facade exports).

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_provisioner.py
```

Result: collection error, `ActiveCompletionLease` is not exported. This is diagnostic red evidence only, not an
acceptance run or green claim.

## Gate

**Build remains blocked.** Repair MUST-1 through MUST-12 with behavioral red tests first. The persisted recovery proof
gap in MUST-2/MUST-3 requires an explicit public-API decision before provisioner edits; it must not be patched with
private helpers, direct filesystem inference, or another synthetic state/fallback.
