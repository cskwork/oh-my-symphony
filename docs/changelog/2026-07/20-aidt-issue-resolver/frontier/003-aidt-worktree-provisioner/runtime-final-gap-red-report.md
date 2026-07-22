# Frontier 003 Runtime Final-Gap RED Report

Date: 2026-07-21
Scope: test-only closure of runtime final-verifier findings R1-R4
Verdict: **RED CAPTURED / CURRENT PRODUCT REPLAY GREEN**

## Decision and theory

Three of the eight frozen public runtime tests now fail on the four missing runtime boundaries. The regressions use
only public DTO/runtime calls, fake provisioners, injected clocks, and temporary workflow metadata. They preserve the
existing eight public test names and do not edit runtime, facade, manifest, provisioner, Core, or integration code.

The behavioral boundary is:

1. Every hostile public DTO value closes as exact `AidtWorktreeFailure("internal_error")`; exact scalar types and a
   real whole-second UTC calendar instant are required.
2. A successful prepare consumes/counts its issued action before any later clock or publication postcheck failure.
3. Admission and guard DTOs are issued capabilities, not forgeable value bags. Admissions are atomically one-use,
   guards come only from successful prepare, generation/rejection rotates them closed, and cleanup accepts only the
   exact owned path.
4. Runtime construction derives metadata lexically from a precomputed absolute workflow path and performs no
   `Path.resolve` or metadata I/O.

## Installed RED coverage

- `test_never_enabled_unmanaged_runtime_is_inert` aggregates all hostile DTO outcomes plus the constructor sentinel.
  It retains the existing frozen/redacted DTO assertions.
- `test_health_counts_create_resume_failure_and_sanitizes_last_detail` covers both provision and resume. In each row
  fake prepare returns successfully, then the injected clock becomes invalid. The expected outcome is owned
  `clock_invalid`, one prepare, the matching action count at one, one failure, and fatal status.
- `test_delegate_converts_post_recognition_exceptions_to_owned_error` now obtains its ordinary attestation guard from
  a successful create. Its capability helper covers an unissued guard, forged admission pair, two-thread admission
  reuse, forged issued-guard pair, and mismatched cleanup path. Every rejected case asserts zero downstream calls.
- `test_stale_or_failed_reload_generation_cannot_reach_backend_barrier` now rotates genuinely issued admission and
  guard capabilities across material publication and reload rejection, while retaining exact no-delegation checks.

The independent R3 subagent's shared-file changes were inspected and integrated after its interrupted turn. No
subagent product edit was accepted.

## Exact RED evidence

Collection:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_aidt_worktree_runtime.py
8 tests collected in 0.25s
```

Focused runtime:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_runtime.py
3 failed, 5 passed in 1.12s
```

The three failing public tests are exactly the DTO/constructor aggregate, issued-capability boundary, and
post-prepare counter boundary. A bounded diagnostic replay produced:

```text
R1_R4 ['generation-settings:AttributeError',
       'health-status-subclass:accepted',
       'health-status-list:TypeError',
       'health-calendar:accepted',
       'constructor:AssertionError']
R2 provision owned_error clock_invalid prepare=1 create=0 resume=0 failure=1 fatal
R2 resume    owned_error clock_invalid prepare=1 create=0 resume=0 failure=1 fatal
R3 unissued_guard     handled          attest=1
R3 forged_admission   handled          prepare=1
R3 admission_race     handled,handled  prepare=2
R3 forged_guard       handled          attest=1
R3 wrong_path         owned_preserved authorization_invalid cleanup=1
```

These are root-cause failures. No expectation was weakened to accept the current product.

## Unaffected controls and static gates

Accepted contract/manifest/provisioner-facade controls:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_contract.py tests/test_aidt_worktree_manifest.py \
  tests/test_aidt_worktree_provisioner.py::test_public_facade_exports_exact_provisioner_surface_lazily_in_all_orders
125 passed in 2.06s
```

Static evidence:

```text
../../.venv/bin/ruff check --no-cache tests/test_aidt_worktree_runtime.py
All checks passed!

../../.venv/bin/pyright tests/test_aidt_worktree_runtime.py
0 errors, 0 warnings, 0 informations

Test AST structure scan
functions 82
max_function_lines 47
max_nesting 3
over_50 []
over_nesting_4 []

git diff --no-index --check /dev/null tests/test_aidt_worktree_runtime.py
exit 1 with no output

git diff --no-index --check /dev/null \
  docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/runtime-final-gap-red-report.md
exit 1 with no output
```

The no-index exit is the expected content-difference status for untracked files; empty output proves no whitespace
finding.

No Git command, network call, live repository, backend, Jira mutation, commit, or product write was performed by the
test-only RED task. Product repair was assigned separately; its current replay follows.

## Recovery-capability correction and current replay

The post-RED compatibility review found one frozen-test contradiction: `_assert_reload_recovery` used a synthetic
guard after `reject_reload`, although R3 requires rejection to clear issued capabilities and forbids unissued guards.
The recovery probe now equivalent-publishes the recovered current generation, obtains a fresh durable admission,
successfully prepares it into an issued guard, and calls `before_run` with that exact guard. The exact attestation call
is asserted. No R3 expectation was weakened.

After the separately assigned product repair landed, the corrected focused replay is:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_runtime.py
8 passed in 1.35s

../../.venv/bin/ruff check --no-cache tests/test_aidt_worktree_runtime.py
All checks passed!

../../.venv/bin/pyright tests/test_aidt_worktree_runtime.py
0 errors, 0 warnings, 0 informations

Test AST structure scan
functions 82
max_function_lines 47
max_nesting 3
over_50 []
over_nesting_4 []
```

The earlier three-failure evidence remains the exact pre-repair RED baseline; this final replay proves the same eight
public tests, including genuine recovery capability issuance, are now green.
