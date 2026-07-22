# Frontier 003 provisioner RED-test fixture design

Date: 2026-07-21

## Decision

Use one provisioner-local aggregate fixture backed by a disposable SHA-1 repository, real coordinator/child route cards,
the real route loader and binding observer, the real manifest registry, and the real public Git-state API. Double only the
production fetch transport, deterministic drift timing, completion authority, crash points, and durable-write failures.
Never construct a `RepositoryState`, snapshot digest, route-pair digest, or repository-binding digest in the test.

This is test design only. It authorizes no product/schema change and no network, live checkout, Jira, generic workspace
fallback, or project commit. The landed recovery operations are the restart oracle: `prove_prepared_recovery`,
`prove_ready_recovery`, and `prove_removed_recovery` (`git_state.py:464-628`).

## Fixture object

Place the shared implementation in `tests/aidt_provisioner_support.py`; tests stay in
`tests/test_aidt_worktree_provisioner.py`. The aggregate has these exact fields:

```python
@dataclass
class ProvisionerFixture:
    root: Path
    aidt_root: Path
    service_root: Path
    bare_origin: Path
    board_root: Path
    workflow_path: Path
    workspace_root: Path
    identifier: str
    coordinator: str
    service_id: str
    branch: str
    old_base_sha: str
    base_sha: str
    config: ServiceConfig
    settings: AidtWorktreeSettings
    paths: StableWorktreePaths
    route: AidtRouteDispatchContract
    binding: ObservedService
    clock: FixedClock
    events: EventRecorder
    runner: RecordingFetchRunner
    route_loader: SequencedRouteLoader
    binding_observer: SequencedBindingObserver
    authority: RecordingAuthority
    fault_hook: FaultController
    write_faults: WriteFaultController
```

Required methods are `provisioner()`, `restart()`, `initial_admission()`, `current_admission()`, `prepare_ready()`,
`authorization(**overrides)`, `lease(**overrides)`, `durable_bytes()`, and `assert_no_forbidden_commands()`.
`restart()` creates a new provisioner and new one-shot seam cursors while retaining the same Git repository, cards,
registry, clock instant, and recorder. It performs no repair itself.

### Value objects used by the fixture

```python
@dataclass(frozen=True)
class Event:
    sequence: int
    source: Literal["route", "binding", "git", "authority", "fault", "durable"]
    action: str
    detail: tuple[tuple[str, str], ...] = ()

@dataclass(frozen=True)
class CommandCall:
    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...]
    timeout: float
    stdout_cap: int
    stderr_cap: int

@dataclass(frozen=True)
class DurableBytes:
    manifest: bytes | None
    ownership: bytes | None
    attempt: bytes | None

@dataclass(frozen=True)
class DurableWrite:
    kind: Literal["manifest", "ownership", "attempt"]
    record_revision: int
    manifest_revision: int | None
    state: str
    path: Path
    data: bytes
    sha256: str
```

`EventRecorder.record()` allocates the sequence number under a `threading.Lock`. Tests assert ordered `action` slices,
not incidental read-only Git commands. The runner keeps full `CommandCall` values separately for exact argv/environment/
timeout/cap assertions. Event details contain only bounded identifiers, revisions, categories, and digests; never paths,
lease tokens, stderr, or exception text.

## Disposable construction

1. Create `aidt/viewer-api` with the local-only helpers already used by routing and Git-state tests:
   `frozen_git_repository`, `git_command`, `routing_config`, `service_config`, and `service_definition`
   (`tests/aidt_routing_support.py:25-167`). Include `pom.xml`, `.gitignore`, `src/Route.java`,
   `src/Domain.java`, and one ordinary tracked file. Add only the canonical inert HTTPS origin
   `https://fixture.invalid/repository.git`.
2. Keep the helper's first production commit as `old_base_sha`. Create a second local `aidt-prd` commit as `base_sha`,
   switch the root checkout back to its unrelated branch, and add tracked, untracked, and ignored root dirt. Clone a
   local bare evidence repository whose `refs/heads/aidt-prd` is `base_sha`; the production runner may read this bare
   ref but must never fetch from it.
3. While `refs/remotes/origin/aidt-prd == base_sha`, call real `observe_service_binding` and retain the returned
   `ObservedService`. Build the catalog observation with that exact SHA, binding digest, and committed route/domain
   bytes. Resolve the Jira-shaped source with `resolve_card`, write the coordinator with `write_ticket_atomic`, and
   create the child with `apply_route_resolutions`, following `test_aidt_route_dispatch_contract.py:86-150`.
4. Add `aidt_worktree: {enabled: true}` to the same raw profile and replace only the fields proven by
   `test_aidt_worktree_contract.py:30-53`: absolute workflow/workspace roots,
   `workspace_reuse_policy="preserve"`, all five
   generic hooks absent, and auto-commit/auto-merge false. Obtain `settings` through
   `load_aidt_worktree_settings`; do not instantiate it directly.
5. Load `route` through real `load_route_dispatch_contract` and assert it equals a second fresh load. Assert
   `route.checkout_revision == base_sha`, `route.repository_binding_digest == binding.repository_binding_digest`,
   and `route.branch == "fix/A20-1188"`. Parse both cards and assert their stored route/coordinator fingerprints and
   repository-binding values are byte-equal to the contract; the two fresh loads must have the same route-pair digest.
   Only now restore the fixed remote ref to `old_base_sha`, creating the genuine S0-to-S1 fetch delta.
6. Call `activate_registry(settings.paths, settings.workflow_identity, clock.text)`. Persist a real
   `initial_attempt_record`, then consume it through `admit_attempt(..., scope_attested=True)`. The default admission is
   built from the resulting revision-2 record, never a guessed revision. This models the exact initial admission
   contract in `provisioner-test-brief.md:120-130`.

The fixture root contains all state. It neither reads nor writes the checkout containing this test suite.

## Deterministic seams

### Clock

`FixedClock` has fields `instant: datetime` and `calls: int`. `__call__` increments `calls` and returns the same
whole-second UTC instant (`2026-07-21T01:02:03Z`). `advance(seconds)` is explicit and is used only by backoff tests.
Equal transition timestamps are valid and keep restart golden bytes independent of internal call count.

### Route and binding

`SequencedRouteLoader` fields are `real_loader`, `events`, `call_index`, and
`replacements: dict[int, AidtRouteDispatchContract | None | Exception]`. An unplanned call invokes the real loader.
A planned call returns/raises the indexed replacement after recording `route:load:<n>`. A drifted contract is made by
`replace(fixture.route, route_pair_digest=<other 64 hex>)`; all baseline calls remain fresh card attestations.

`SequencedBindingObserver` has the same shape, using the real observer and
`replacements: dict[int, ObservedService | Exception]`. Binding drift uses `replace(fixture.binding,
repository_binding_digest=<other 64 hex>)` or a different `checkout_revision`. Call-index plans permit drift exactly
after fetch, immediately before add, or after the outer/pre-lock observation. Clear replacement plans on restart.

### Runner and command guard

Reuse the `_ProductionFetchDouble` behavior at `tests/test_aidt_worktree_git_state.py:987-1020`, extended only with the
shared recorder and one-shot result overrides. `RecordingFetchRunner` fields are `base_sha`, `bare_origin`, `events`,
`calls`, `fetch_calls`, and `overrides: dict[int, GitCommandResult | Exception]`.

- If `argv == FETCH_ARGV`, assert the bare fixture's production ref is `base_sha`, record the exact call, update only
  the disposable service's fixed remote ref to `base_sha`, and return `GitCommandResult(0, b"", b"")`.
- Every other command delegates to `default_binary_runner` with the exact supplied arguments.
- The guard rejects any fetch other than `FETCH_ARGV`; any mutation other than exact public add/plain-remove vectors;
  and every `reset`, `rebase`, `switch`, `checkout`, `prune`, `--force`, `-D`, branch-delete, or filesystem-delete
  fallback. Compare the mutation list with the vectors already asserted at
  `tests/test_aidt_worktree_git_state.py:1072-1129`.

Do not stub `observe_repository_state`, the three delta validators, classification, ticket observation, ancestry, or
the three recovery proof operations.

### Authority and lease

`RecordingAuthority` fields are `events`, `answers: deque[bool | Exception]`, and
`calls: list[tuple[CompletionAuthorization, ActiveCompletionLease, AidtWorktreeManifest,
AidtRouteDispatchContract]]`. It returns the next exact answer and otherwise `False`.

`authorization()` fills all 13 `CompletionAuthorization` fields: schema, fixture identifier/generation/pair, ready
revision 2, matching issue/run/attempt/lease token, a 64-hex final-transition identity, issuer
`aidt-stage-controller-v1`, fixed issued time, and a 64-hex authorization digest. `lease()` fills all six
`ActiveCompletionLease` fields with the same identifier/issue/run/attempt, `active=True`, and
`competing_owner=False`. Keyword overrides drive the negative matrix. No production authority implementation is used.

### Crash hooks

`FaultController` fields are `events`, `armed: str | None`, and `seen: list[str]`. It rejects unknown seam names and,
when the armed name is observed, disarms and raises local `SimulatedProcessCrash(BaseException)`. Using a
`BaseException` models process loss through the public method without the draft's ordinary `Exception` failure-record
path. Restart assertions cover exactly the six names in `provisioner-test-brief.md:204-211`; no extra hook name is
introduced.

### Per-write failure and canonical capture

Capture the original public `persist_manifest`, `persist_ownership`, and `persist_attempt`, then monkeypatch the names
imported by the provisioner module. `WriteFaultController` fields are `events`, `writes: list[DurableWrite]`,
`armed: tuple[str, int, Literal["before", "after"]] | None`, and per-kind `counts`.

Each wrapper computes `canonical_json_bytes(record)`, optionally fails before the selected occurrence, calls the
original CAS writer, asserts `path.read_bytes()` equals the canonical bytes, records `DurableWrite`, and optionally
raises `SimulatedProcessCrash` after that write. Label ownership with `state="tombstone"` when `tombstone=True`; otherwise
use `manifest:<state>`, `ownership:r<manifest_revision>`, or `attempt:<mutation_phase>/<disposition>`. This supports a
restart after every manifest, ownership, attempt, and final tombstone write without touching `_atomic_replace` or any
private persistence helper.

For every stable point assert:

```python
assert fixture.paths.manifest.read_bytes() == canonical_json_bytes(read_manifest(...))
assert fixture.paths.ownership.read_bytes() == canonical_json_bytes(read_ownership(...))
assert fixture.paths.attempt.read_bytes() == canonical_json_bytes(read_attempt(...))
```

Also compare unaffected `DurableBytes` members byte-for-byte across a rejected transition. Manifest proof fields must
equal the real route/binding values and the genuine public delta/proof results; never assert only `len(digest) == 64`.

## Test mapping

| Finding | Fixture use and binding assertion |
|---|---|
| MUST-1 | No aggregate fixture. Use an isolated `sys.executable -c` import probe: all seven provisioner names lazy-import from the facade; unknown names fail; disabled facade import leaves provisioner/Git-state absent. |
| MUST-2 | Fresh create carries genuine S1 through exact add to genuine S2; cleanup carries genuine cleanup-pre through plain remove to cleanup-post. Restart paths consume `prove_prepared_recovery`/`prove_removed_recovery`. Mutate one unrelated ref and registration and assert preserved bytes plus zero mutation. |
| MUST-3 | Arm `after_physical_remove_before_removed_fsync`; restart accepts only the natural retained-branch/path-and-registration-absent proof. Parameterize branch/path/registration/remote-ref/locked/prunable/wrong-SHA/unrelated drift with runner count unchanged. |
| MUST-4 | `prepare_ready()`, then dirty/commit only the ticket and resume successfully. Independently alter root, protected occupancy, unrelated refs/registrations, target path/branch/upstream, or non-descendant HEAD; `prepare`/cleanup must not mutate. |
| MUST-5 | Plan nth-call route and binding replacements for before-run, ready cleanup, exact removing retry, and proof-only finalization. Assert failure precedes authority/backend/remove/removed write events. |
| MUST-6 | Use keyword overrides over the exact authorization/lease builders. Authority returns true, but every revision/generation/pair/digest/active/competing/identity/run/token/attempt mismatch preserves; only revision 2 plus the same active lease reaches removing. |
| MUST-7 | Snapshot attempt bytes, present stale/same-revision admissions after another call reaches ready or resets scope, and use recorder gates around public lock entry when exercising the two-thread delayed-failure case. The newer bytes remain exact and no all-zero/fabricated common lock event is allowed. |
| MUST-8 | Arm every `(kind, occurrence, "after")`, call only public `prepare`/`cleanup`, restart, and require convergence from the permitted predecessor or permanent preservation from conflicts. Fetch/add/remove counts remain at most one. |
| MUST-9 | Build admissions/guards only through public DTOs, then `replace` each field with another valid scalar; seed a valid same-revision other-child/scope attempt through public persistence. Fail before Git/backend. The exact returned guard passes immediately and after restart. |
| MUST-10 | Arm each of the six `FaultController` seams, assert `SimulatedProcessCrash`, create a new provisioner, and assert the documented command sequence with no failure-record write between crash and restart. |
| MUST-11 | The 13 frozen test names use this aggregate and assert event order, record revisions/canonical bytes, result/guard fields, `created_now`, preservation, and forbidden effects; method-existence assertions are removed. |
| MUST-12 | Type the aggregate with `StableWorktreePaths`, concrete protocol callables, and exact DTOs; no `Any` for path families. Run Pyright on product plus support/test files and retain one runtime exact-type assertion for `PreparedAidtWorktree.result/guard`. |
| SHOULD-1 | Wrap the public `fetch_production_base` at the provisioner import boundary only for this negative case: delegate real work, then return a mismatched `FetchResult`. For the invalid-proof case only, wrap the public recovery call after a real proof and forge an exact-type copy with missing registration evidence. Assert no prepared/ready write; all positive/recovery evidence still uses the unmodified public operations. |
| SHOULD-2 | Construct hostile but valid absolute path/run-token sentinels and assert absent from DTO repr, recorded event detail, bounded failure, and any health/log payload in scope. |
| SHOULD-3 | `DurableWrite.data` is the golden: assert keyword-built manifest/ownership/proof records produce unchanged canonical bytes at prepared, ready, removing, and removed. |

The 13 names in the brief divide as follows: create/order and both drift tests use the fresh fixture; the three prepared
tests use named crash/restart states; ready and before-run share the ready projection setup; attempt ordering uses write
capture; the three cleanup tests share authority/lease plus removing crash states; the final safety test aggregates every
runner and persistence failure override and invokes the command guard.

## Reuse boundary

Reuse unchanged:

- routing/config/local-Git builders in `tests/aidt_routing_support.py`;
- route-card construction from `tests/test_aidt_route_dispatch_contract.py:86-150`;
- exact production fetch behavior and command assertions from
  `tests/test_aidt_worktree_git_state.py:987-1129`;
- real recovery mutation helpers and race shapes from
  `tests/test_aidt_worktree_recovery_proofs.py:35-179`;
- canonical record builders/readers/CAS assertions from
  `tests/test_aidt_worktree_manifest.py:106-223,247-319`.

Add only the aggregate, unified recorder, sequenced route/binding seams, authority/lease builders, crash controller, and
write controller. Do not promote them to product utilities: they encode one Frontier003 lifecycle and test-only crash
semantics. The current in-progress `tests/aidt_provisioner_support.py` is a suitable location, but its fabricated route,
pair digest, binding digest, and direct `SimpleNamespace` binding must be replaced by the real card/observer construction
above before its results count as acceptance evidence.

## Verification gate

After the RED suite is installed, run only local commands: focused pytest, Ruff, Pyright, then the route/manifest/
Git-state/recovery regression files. A passing skeleton or a test that replaces a Git proof operation is not evidence.
No test may use a live repo, real fetch, network, Jira, force removal, raw directory deletion, private Git-state helper,
or direct provisioner filesystem inference. Add a small AST guard for direct `subprocess`, `shutil.rmtree`, raw
workspace unlink/rmdir, and forbidden Git tokens so a bypass outside the injected runner cannot escape the command
recorder.
