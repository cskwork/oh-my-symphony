# Frontier 003 Runtime RED Test Report

Date: 2026-07-21
Scope: runtime behavioral tests and evidence only; no runtime/product implementation

## Decision and theory

Eight executable RED tests now bind PLAN Binding Amendment 4. A private immutable publication key controls generation
identity; durable manifest/ownership/attempt records retain worktree ownership; fresh route attestation establishes
current recognition; attempt CAS grants one provisioning admission; provisioner prepare returns the run guard; and a
current-generation before-run attestation is the final backend barrier. Only exact `UNMANAGED` permits later generic
behavior.

The test-local import helper loads `symphony.aidt_worktree.runtime` only during test execution, so the suite also
collects cleanly when the runtime is absent. Fixtures use a fake provisioner and temporary strict metadata only. No
Git runner/repository, network, Jira, backend, live AIDT checkout, `WorkspaceManager`, Core, schema, route, facade, or
external state is mutated.

## Executable M1-M10 closure

1. Lazy/default-off: the frozen constructor default is `None`; a fresh subprocess constructs and disabled-publishes a
   runtime while provisioner, manifest, and Git-state remain unloaded and no metadata appears. DTO shape, validation,
   frozen state, redacted representation, facade exports, and unknown-name behavior are also bound.
2. Disabled path: only an exact validated durable manifest plus ownership/tombstone may return the original recorded
   path from `path_for`; missing/corrupt evidence is owned error and mutating gates remain preserved.
3. Unmanaged distinction: zero-loader controls use non-child `LOCAL-1`; a canonical current route child is separately
   asserted owned-preserved after disable, while a different canonical child whose route loader returns exact `None`
   is `UNMANAGED` after one exact loader call.
4. Ready evidence: positive resume persists aligned ready manifest, ownership, and attempt. Missing manifest and
   mismatched owner cases persist a manual `registry_invalid` failure without calling prepare. Every tracked
   final ready-validation manifest, ownership, attempt reread, and manual-persist operation occurs while the exact
   `manifest_lock` is active. The binding-mandated pre-lock `read_optional_attempt` plus its nested public
   `read_attempt` call form the sole exact inactive observation cluster; every later tracked operation must be active.
5. Atomic publication: material activation and factory failures publish no generation/provisioner, do not count in
   `publish`, count once through bounded `reject_reload`, and allow recovery to the exact old DTO/provisioner.
6. Fatal circuit: activation, initial write, consume, scope reset, provisioner `persistence_failed`, naive clock, and
   aware non-UTC `+09:00` clock each latch once. Equivalent publication may return the old DTO;
   changed/disabled publication raises; repeated delegate and reload rejections do not inflate health.
7. Counter timing: a successful `provision` prepare with `created_now=False` increments create before a concurrent
   publication postcheck returns `scope_changed`; resume counting remains action-based.
8. Exception table: actual `AidtRoutingFailure` maps to owned `card_invalid`; bounded worktree failures retain category
   and public identifier ref; unexpected prepare/attest/cleanup failures map to `internal_error`; factory failure stays
   publication-scoped.
9. Health/structure: health is called twice with clock, route, manifest/registry, provisioner, `os.open`, `os.scandir`,
   and `Path.lstat` replaced by raising sentinels, and all three provisioner call lists remain unchanged. A product AST
   gate rejects direct, relative, façade-export, constant, and dynamic private Git-state imports, plus tracker/network
   imports, while also enforcing every runtime function at 50 lines/four nesting levels.
10. Registration-only recognition without a current route or durable record is explicitly deferred. The runtime AST
    gate forbids private Git-state parsing/observation through both module and façade exports; current-route and
    durable-record ownership remain final.

The admission test also binds initial/manual/non-due/due/scope-reset/ready transitions and a real two-thread barrier:
both runtimes read absence concurrently, one receives the revision-2 admission, the CAS loser is owned
`cas_mismatch`, one consumed record remains, and neither fake provisioner is called. Post-publication mutation of the
shallow `ServiceConfig.raw` is followed by a fresh equivalent publish to require the private immutable material key.

## Exact evidence

Collection:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q tests/test_aidt_worktree_runtime.py
8 tests collected in 0.20s
```

Current runtime replay:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_runtime.py
1 failed, 7 passed in 0.82s
```

The corrected ready-lock probe accepts exactly the public pre-lock nested observation and rejects every later inactive
read or persist. The sole failure precedes the probe assertion: missing ready evidence returns owned
`registry_invalid`, but the durable attempt remains `ready` instead of becoming manual. An independent no-probe replay
produces the same record. The public `next_failure_record` ready `added`/revision-2 transition is the bounded remaining
manifest seam covered by PLAN Binding Amendment 5; no runtime lock assertion was weakened.

Unrelated accepted metadata/contract baseline:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_contract.py tests/test_aidt_worktree_manifest.py \
  tests/test_aidt_worktree_provisioner.py::test_public_facade_exports_exact_provisioner_surface_lazily_in_all_orders
124 passed in 2.25s
```

Strict public-record self-check:

```text
Temporary prepared -> ready -> removing -> removed manifest/tombstone discovery
Temporary ready manifest/ownership/attempt discovery
ready_removed_fixture_ok
```

Static evidence:

```text
../../.venv/bin/ruff check --no-cache tests/test_aidt_worktree_runtime.py
All checks passed!

../../.venv/bin/pyright tests/test_aidt_worktree_runtime.py
0 errors, 0 warnings, 0 informations

Test AST structure scan
functions 73
over_50 []
over_nesting_4 []
max_function_lines 47
max_nesting 2

Runtime import-boundary synthetic self-check
runtime_import_boundary_ok

git diff --no-index --check /dev/null tests/test_aidt_worktree_runtime.py
exit 1 with no output

git diff --no-index --check /dev/null \
  docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/runtime-red-test-report.md
exit 1 with no output
```

The no-index exit is the expected content-difference status for an untracked file; no output means no whitespace
error. The in-test product AST gate becomes executable as soon as the RED runtime module lands.

## Frozen amendments and residual

- `path_for` alone may return `HANDLED(recorded_path)` while disabled/rejected after exact durable proof; every
  mutating delegate stays closed.
- The default provisioner factory is lazy `None`; enabled publication resolves the production factory and calls it as
  `factory(config, settings, clock=clock)`.
- Standalone orphan-registration recognition remains deferred because no public observer exists. No private Git
  parser or impossible fixture was added.
- The runtime/facade implementation now passes seven of eight tests. The sole remaining blocker is the independently
  assigned Binding Amendment 5 public manifest transition from ready `added`/revision 2 to persisted manual
  `registry_invalid`; this test-only slice does not authorize that product change.

The assigned worktree contains `AGENTS.md` but no `CLAUDE.md`; that absence was verified before editing. Existing
unrelated dirty/untracked Frontier files were preserved.
