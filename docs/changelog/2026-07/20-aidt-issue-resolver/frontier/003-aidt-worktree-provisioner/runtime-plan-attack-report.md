# Frontier 003 runtime plan attack report

Date: 2026-07-21
Scope: runtime plan, landed public worktree APIs/facade, and the current runtime RED suite
Verdict: **FAIL / REQUEST CHANGES before Build**

## Summary

The runtime is correctly bounded as a process-lifetime ownership/admission coordinator rather than a second Git or
manifest engine. The landed CAS, delegate, provisioner, and sanitization APIs can support most of that role. Build is
not safe yet because the frozen documents and RED suite disagree on lazy construction, disabled path resolution,
ready admission, counter timing, and registration-only recognition. Several named RED tests also do not exercise the
failure source their name freezes.

Inspection snapshot: `tests/test_aidt_worktree_runtime.py` was 775 lines and
`src/symphony/aidt_worktree/runtime.py` did not exist at the final read. The RED file changed during this review; this
report uses the later version containing full manifest fixtures. `AGENTS.md` was read. There is no `CLAUDE.md` in this
worktree, and the no-live-repository constraint was preserved.

## Prioritized ledger

| ID | Priority | Status | Boundary | Decision |
|---|---|---|---|---|
| M1 | MUST | FAIL | Lazy imports / factory default | The exact default eagerly imports the heavy provisioner and Git-state graph. Amend the default to a lazy `None`/wrapper. |
| M2 | MUST | FAIL | Ownership before disabled gate | `path_for` needs a documented non-mutating exception to the universal generation gate. |
| M3 | MUST | FAIL | Never-enabled unmanaged control | A canonical child cannot be declared unmanaged with zero route lookup unless durable evidence already decides it. |
| M4 | MUST | FAIL | Ready admission | Positive fixtures now carry manifest evidence, but missing/mismatched ready evidence is neither mapped nor tested. |
| M5 | MUST | FAIL | Atomic publication | Equivalent/change races are tested; activation/factory failure before publication is not. |
| M6 | MUST | FAIL | Fatal circuit | Only initial-attempt persistence failure is covered; fatal sources and post-fatal publication remain incomplete. |
| M7 | MUST | FAIL | Counter semantics | The brief counts a successful ready transition; the map delays the count until after a stale-publication postcheck. |
| M8 | MUST | FAIL | Exception mapping | The RED loader raises the wrong exception family and factory failure is unused. |
| M9 | MUST | FAIL | Health/structure gates | No-I/O health and function-length/nesting requirements have no executable/static assertion. |
| M10 | MUST | FAIL | Registration-only ownership | The PLAN requires recognition that no current public API can perform. Explicitly defer it; never parse private Git state. |
| P1 | MUST | PASS | Idempotent generation | Equivalent publication identity and changed-generation staleness are correctly frozen. |
| P2 | MUST | PASS | Attempt initialization/CAS | Public manifest primitives support revision-1 initialization followed by atomic consumption. |
| P3 | MUST | PASS | Scope reset | The public admission helper performs attested reset-and-deny, then a later tick may consume. |
| P4 | MUST | PASS | Injected factory signature | `factory(config, settings, clock=clock)` matches the landed keyword-only constructor. |
| P5 | MUST | PASS | Sanitized delegate results | The sealed four-way result and bounded worktree failure are suitable final mappings. |
| P6 | SHOULD | PASS | Cold facade pattern | The existing facade can add a closed runtime export set without loading it on cold import. |
| S1 | SHOULD | FAIL | Published config immutability | `ServiceConfig` is frozen only shallowly; its mutable `raw` dict can invalidate equality assumptions. |
| S2 | SHOULD | FAIL | Concurrent initializer proof | The APIs are sufficient, but the current RED suite does not exercise the CAS loser. |

## MUST findings

### M1 — Exact factory default and disabled lazy import are mutually incompatible

Evidence:

- The runtime signature freezes `provisioner_factory=AidtWorktreeProvisioner` at
  `runtime-implementation-map.md:81-88` and repeats the exact invocation at `:132-134`.
- Disabled startup must not eagerly load provisioner or Git-state at `core-integration-test-brief.md:48-49`; the PLAN
  repeats disabled lazy loading at `PLAN.md:371-374`.
- Importing the landed provisioner imports `.git_state` immediately at
  `src/symphony/aidt_worktree/provisioner.py:33-75`; the class is defined at `:218`.
- The RED subprocess checks only the cold facade at `tests/test_aidt_worktree_runtime.py:274-298`; it does not assert
  that obtaining/constructing a disabled runtime leaves provisioner and Git-state unloaded.

Smallest correction:

```python
def __init__(
    self,
    workflow_path: Path,
    *,
    clock: Callable[[], datetime],
    provisioner_factory: ProvisionerFactory | None = None,
) -> None: ...
```

Resolve `None` by importing `AidtWorktreeProvisioner` only inside the first valid enabled publication. Keep runtime
annotations postponed and provisioner DTO imports lazy. An equivalent module-local lazy wrapper is acceptable, but
the documents must stop claiming the eager class object is the exact default. Extend the subprocess to construct and
publish a disabled runtime before asserting that provisioner/manifest/Git-state remain absent.

### M2 — `path_for` conflicts with the universal disabled/rejected gate

Evidence:

- The map says every delegate recognizes ownership and then maps recognized disabled/rejected state to
  `OWNED_PRESERVED` at `runtime-implementation-map.md:179-194`.
- The current restart fixture requires a disabled runtime to return the original durable tombstone path as
  `HANDLED(path)` at `tests/test_aidt_worktree_runtime.py:575-585`.
- Durable paths must survive root replacement and disable at `PLAN.md:333-351` and
  `provisioner-test-brief.md:173-184`.

Smallest correction — controlling amendment:

> `path_for` alone may `HANDLED`-return an exact validated durable recorded path under a disabled or rejected
> generation because it is non-mutating. Every `admit_candidate`, `create_or_reuse`, `before_run`, and `remove`
> generation gate remains preserved/error. Corrupt, missing, or conflicting durable evidence remains owned error;
> only exact absence plus no current route is unmanaged.

This exception must appear in the map's gate table and in the runtime method rules; it must not be generalized to
create, backend, or cleanup authority.

### M3 — The no-route unmanaged fixture uses a canonical child

Evidence:

- `OTHER` is `A20-9999--viewer-api`, a valid managed-child shape, at
  `tests/test_aidt_worktree_runtime.py:60`.
- The never-enabled test expects `path_for` and `admit_candidate` to return `UNMANAGED` and then asserts zero loader
  calls at `tests/test_aidt_worktree_runtime.py:502-535`.
- Current route ownership is an authoritative recognition source, and only loader `None` proves it absent, at
  `runtime-implementation-map.md:181-205`.

The expectation is impossible as stated: without a route read or durable record, a canonical child can be either a
managed child or an unmanaged lookalike.

Smallest correction — controlling amendment: use a truly non-child generic identifier such as `A20-9999` for the
zero-loader negative control. Add a separate disabled canonical child whose loader returns a route and assert final
owned preservation. A canonical child may be `UNMANAGED` only after the bounded loader returns `None`.

### M4 — Ready admission still lacks a negative evidence contract

Evidence:

- The final PLAN amendment requires a ready record with missing/mismatched manifest to become manual owned failure at
  `PLAN.md:552-559`.
- The mapped admission algorithm reads an attempt and maps ready directly to resume at
  `runtime-implementation-map.md:226-247`; it does not require ready manifest/ownership equality.
- The public `admit_attempt` likewise admits ready solely from the attempt record at
  `src/symphony/aidt_worktree/manifest.py:669-695,1463-1480`.
- The current positive fixture now correctly persists manifest states at
  `tests/test_aidt_worktree_runtime.py:411-438` and aligns manifest/ownership/attempt at `:466-488`, but no test removes
  or mismatches one of those records before admission.
- The real provisioner validates ready evidence only later in `prepare` at
  `src/symphony/aidt_worktree/provisioner.py:246-258,592-609`; that is after Core admission/task selection and is not a
  substitute for the frozen durable-admission gate.

Smallest correction: before returning a ready `HANDLED(admission)`, re-read exact public manifest, ownership, and
attempt evidence under the manifest lock and require ready state, revisions, route pair, generation, path, and
non-tombstone owner alignment. Missing/mismatch must be persisted as a manual owned failure. Add negative missing and
mismatch cases. If this cannot remain a small runtime helper, add one narrow public manifest validation helper; do not
call `provisioner.prepare` during candidate admission and do not copy private provisioner helpers.

### M5 — Failed publication is named but not executed

Evidence:

- Publication must construct/activate off to the side and publish nothing on validation, activation, or factory
  failure at `runtime-implementation-map.md:156-177`.
- Core relies on that behavior before manager replacement at `core-integration-test-brief.md:107-126`.
- The RED reload test covers equivalent publication, material change, explicit `reject_reload`, and races at
  `tests/test_aidt_worktree_runtime.py:539-572`. `FakeFactory.error` is defined at `:137-140` but never used.

Smallest correction: inject one generic factory failure and one `activate_registry` failure during a material reload.
Assert the current DTO identity, revision, provisioner, and manager-facing generation do not change; the gate closes
only through the bounded rejection path; and health increments once, not once in `publish` plus again in
`reject_reload`.

### M6 — Fatal coverage and post-fatal publication are incomplete

Evidence:

- Fatal sources are `persistence_failed`, `durability_failed`, and `clock_invalid` at
  `runtime-implementation-map.md:262-274`; repeated fatal rejection must not increment health at `:276-288`.
- The PLAN requires initial, consume/reset, and restart persistence behavior at `PLAN.md:526-540`.
- The current fatal test patches only the runtime's initial `persist_attempt` call at
  `tests/test_aidt_worktree_runtime.py:709-737`. It does not cover activation, consume, scope reset, provisioner
  `persistence_failed`, or invalid/non-UTC clock.

Smallest correction — controlling amendment:

- Parameterize activation, initial-record, consume/reset, provisioner failure-record loss, and invalid clock.
- The triggering operation increments failure exactly once and latches its sanitized category.
- Equivalent publication after fatal may return the exact current DTO, as the existing test expects.
- A materially changed or disabled publication after fatal must raise the latched bounded failure without replacing
  current generation/provisioner; Core must return before manager replacement.
- `reject_reload` and repeated delegate calls after an open fatal circuit do not increment failure again.

### M7 — Create/resume counters disagree at the stale postcheck

Evidence:

- The frozen brief says absent/prepared entry increments create once when it reaches ready, and ready entry increments
  resume once at `provisioner-test-brief.md:173-179`.
- The implementation map says increment only after the final publication-token recheck at
  `runtime-implementation-map.md:249-260`.
- A reload during `prepare` is explicitly allowed to leave a safely durable ready worktree before the postcheck fails
  at `runtime-implementation-map.md:150-154`.
- The race test asserts only the final `scope_changed` result at
  `tests/test_aidt_worktree_runtime.py:488-500`; it does not assert counters. The fake also equates
  `created_now` with action at `:92-108`, so prepared-exact recovery is not covered.

Smallest correction — controlling amendment: count the actual successful `prepare` exactly once by
`admission.action`, before the publication postcheck. A later stale postcheck records its owned `scope_changed`
failure but does not erase the physical create/resume count. Add a `provision` admission whose fake result has
`created_now=False`, plus the publication race, and assert create count in both.

### M8 — Exception tests do not match the production exception boundary

Evidence:

- The real route loader raises `AidtRoutingFailure` throughout
  `src/symphony/aidt_routing/dispatch.py:130-170,221-315`; it does not raise `AidtWorktreeFailure`.
- The RED loader case injects `AidtWorktreeFailure("card_invalid")` at
  `tests/test_aidt_worktree_runtime.py:675-686`, so an implementation that mishandles the real exception can pass.
- The map promises a route/discovery/factory/prepare/attest/cleanup exception table at
  `runtime-implementation-map.md:313-318`, but factory failure is not exercised.
- `AidtWorktreeFailure` already sanitizes categories/refs at
  `src/symphony/aidt_worktree/contract.py:81-88`; the provisioner maps ordinary exceptions and failure-persistence loss
  at `src/symphony/aidt_worktree/provisioner.py:1183-1219`.

Smallest correction: freeze and test this table: loader `None` before recognition is unmanaged; production
`AidtRoutingFailure` for a canonical/managed child maps to `OWNED_ERROR("card_invalid")`; an
`AidtWorktreeFailure` preserves its bounded category/ref; every other exception after recognition maps to
`OWNED_ERROR("internal_error")`; factory exceptions are bounded publication failures, not delegate results.

### M9 — No-I/O health and structural bounds are assertions only in prose

Evidence:

- Health must copy locked memory without filesystem/route/Git/registry/tracker/clock reads at
  `runtime-implementation-map.md:290-292` and `core-integration-test-brief.md:217-239`.
- Product functions must be at most 50 lines with nesting at most four at `PLAN.md:145-155`,
  `runtime-implementation-map.md:323-347`, and `core-integration-test-brief.md:286-295`.
- The health test at `tests/test_aidt_worktree_runtime.py:740-775` validates fields/counters/sanitization but installs
  no read/I/O/clock sentinels. The runtime RED file contains no AST structural assertion.

Smallest correction: after creating health state, patch the clock, route loader, manifest/registry readers, and fake
provisioner to raise, then call `health_snapshot` twice and require an identical bounded DTO. Add the promised static
AST gate for runtime product functions; do not count decorators/docstrings as body nesting.

### M10 — Orphan registration-only recognition has no public seam

Evidence:

- The controlling PLAN requires any catalog registration at the deterministic child path to remain owned at
  `PLAN.md:333-340`.
- The map narrows this to registrations reachable through current route or durable ownership at
  `runtime-implementation-map.md:196-205`, then admits the standalone observer does not exist at `:367-370`.
- The authorized runtime slice forbids changes to `git_state.py` and provisioner at
  `runtime-implementation-map.md:326-339`.

The standalone expectation is impossible with current public APIs.

Smallest correction — controlling amendment: explicitly defer registration-only recognition when there is no current
route and no durable ownership/manifest/attempt record. Frontier 003 runtime must not import or call private Git-state
parsers to simulate it. Current-route or durable-record registration proof remains owned. A later slice may add one
bounded public observer and then restore the broader requirement.

## SHOULD findings

### S1 — Generation equality relies on a shallowly frozen config

`ServiceConfig` is frozen, but `raw` is a mutable dict at
`src/symphony/workflow/config.py:435-459`. The map acknowledges this at
`runtime-implementation-map.md:364-366` while requiring exact config equality for idempotence at `:169-173`.

Smallest correction: retain a private immutable material publication key made from validated settings/workflow
generation plus the exact immutable config fields runtime/provisioner consume. Never derive idempotence solely from
equality of the previously stored mutable config object. Keep the DTO field for compatibility and document Core's
ownership/no-mutation requirement.

### S2 — Concurrent initialization is supported but not proven

The public sequence is sound: optional read at `manifest.py:496`, revision-CAS write at `:523-530`, bounded lock at
`:614-644`, initial record at `:734-757`, and atomic consume at `:669-695`. The map requires a competing initializer/CAS
loser to remain owned at `runtime-implementation-map.md:245-247,308-310`, but the current admission test at
`tests/test_aidt_worktree_runtime.py:610-647` is single-threaded.

Smallest correction: add a barrier around two initializers. Require one handled revision-2 admission, one bounded
owned CAS outcome, one durable consumed record, and no duplicate provisioning action.

## PASS evidence

- **Generation idempotence/races:** equivalent config returns the same DTO and material change stales old create/guard
  capabilities in `tests/test_aidt_worktree_runtime.py:539-572`; the map's token recheck at
  `runtime-implementation-map.md:150-177` is the right design.
- **Scope reset:** `evaluate_attempt_admission`/`admit_attempt` own exact revision, attestation, reset, and persistence
  at `manifest.py:646-695,1520-1545`; the RED flow exercises deny-then-readmit at
  `tests/test_aidt_worktree_runtime.py:610-647`.
- **Factory invocation:** the landed constructor requires keyword-only clock at
  `provisioner.py:218-232`, and the fake signature at `tests/test_aidt_worktree_runtime.py:130-140` will reject a
  positional clock.
- **Delegate finality:** the public four-way result is sealed at `contract.py:142-176`; provisioner cleanup never
  returns unmanaged and keeps denied authority preserved at `provisioner.py:296-308`.
- **Ready positive fixture:** the current RED helper now persists public manifest, ownership, and attempt evidence at
  `tests/test_aidt_worktree_runtime.py:411-488`, eliminating the earlier impossible positive resume fixture.
- **Facade shape:** the existing closed lazy sets and `__getattr__` at
  `src/symphony/aidt_worktree/__init__.py:125-223,336-347` can accommodate exactly three runtime exports without a
  reverse import.

## Residual verdict

The runtime slice is implementable after the ten MUST corrections above are made binding. Until then, a builder can
pass the current RED suite while eagerly importing Git-state on disabled startup, admitting incomplete ready state,
partially publishing a failed reload, undercounting a completed create, performing I/O in health, or silently narrowing
the PLAN's ownership promise. **Do not start GREEN against the current frozen map.**
