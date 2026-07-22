# Frontier 003 runtime builder report

Date: 2026-07-21
Scope: runtime composition, exact lazy facade exports, and Binding Amendment 5 manifest seam
Verdict: **GREEN / READY FOR INDEPENDENT VERIFICATION**

## Decision and theory

`AidtWorktreeRuntime` is one process-lifetime coordinator over the landed public route, manifest, and provisioner
APIs. It publishes immutable material generations, recognizes ownership before generation gates, performs durable
attempt admission, delegates all Git lifecycle work to the provisioner, and exposes health from locked memory only.
Only `UNMANAGED` permits later generic workspace behavior.

The implementation keeps disabled startup inert. Importing or constructing the runtime and publishing a disabled
profile does not import provisioner, manifest, or Git-state modules and does not probe or create stable metadata.
Manifest operations are exposed as lazy module seams so the behavioral tests can patch the exact public operation
without introducing eager imports.

## Product changes

- Added `src/symphony/aidt_worktree/runtime.py` with the frozen generation, health, and runtime surface.
- Added exactly `AidtWorktreeGeneration`, `AidtWorktreeHealth`, and `AidtWorktreeRuntime` to the facade's type-checking
  imports, closed runtime export set, `__all__`, and lazy `__getattr__` branch.
- Under PLAN Binding Amendment 5, extended only `_manual_phase_revision` in
  `src/symphony/aidt_worktree/manifest.py` to accept truthful manual `added`/revision-2 records. Exact arbitrary
  revision controls remain closed.

No contract, Git-state, provisioner, workspace, Core, entry, schema, routing, tracker, or network product surface was
changed. Standalone registration-only recognition remains deferred; runtime imports no private Git parser.

## Binding behavior implemented

1. Publication validates a private immutable key over the exact settings and consumed config fields. Equivalent
   publication preserves DTO identity even if the published shallow `raw` mapping is later mutated.
2. Enabled publication validates UTC time, activates the registry, resolves the default provisioner lazily, and
   constructs it before atomic publication. Failed activation/factory construction leaves current state unchanged.
3. Durable recognition precedes gates. `path_for` alone returns an exactly aligned manifest/ownership recorded path
   after disable or rejection; corrupt, missing, conflicting, and partial durable evidence stays owned error.
4. Initial attempt creation uses one pre-lock optional observation followed by exact-lock expected-`None` CAS. A CAS
   loser returns `cas_mismatch` without retry. Public `admit_attempt` owns consume/reset admission.
5. Ready resume is released only after manifest, non-tombstoned owner, and attempt align under the exact manifest lock.
   Invalid evidence persists manual `registry_invalid` with truthful `added`/2 state under that lock.
6. Create/resume counters follow `admission.action` immediately after successful prepare, before the publication
   postcheck. Stale postcheck failure remains separately visible.
7. Fatal `clock_invalid`, `durability_failed`, and `persistence_failed` categories latch once for process lifetime.
   Repeated delegate/reload rejection does not inflate failure health.
8. Route `None` before recognition is unmanaged; `AidtRoutingFailure` maps to `card_invalid`; bounded worktree failure
   retains category/ref; other post-recognition exceptions map to `internal_error`.

## Exact verification evidence

All commands used `PYTHONDONTWRITEBYTECODE=1` where applicable and no cache provider for pytest.

| Gate | Result |
|---|---|
| Amendment 5 regression plus exact revision controls | `5 passed in 0.13s` |
| Complete manifest suite | `92 passed in 0.59s` |
| Complete runtime suite | `8 passed in 0.86s` |
| Contract + manifest + lazy facade baseline | `125 passed in 2.07s` (former 124 plus Amendment 5 regression) |
| Complete provisioner suite | `65 passed in 316.40s` |
| Contract/Git-state/recovery-proof/route-dispatch compatibility | `317 passed in 254.24s` |
| Routing contract/decision/storage/runtime/Git-object compatibility | `190 passed in 30.48s` |
| Ruff on runtime/manifest/facade and runtime/manifest tests | `All checks passed!` |
| Pyright on the same product/test slice | `0 errors, 0 warnings, 0 informations` |
| Runtime product AST | 64 functions; max 34 lines; max nesting 3; no function over 50 or nesting over 4 |
| Lazy/import/static boundary | Covered by all 8 runtime tests, including the fresh disabled subprocess and AST scan |
| Tracked whitespace | `git diff --check` exit 0 with no findings |
| No-index whitespace for runtime/manifest/facade/report | Expected content-difference exit 1 with no findings |

No network, live repository, Jira, backend, AIDT checkout, Git mutation, or commit was used.
