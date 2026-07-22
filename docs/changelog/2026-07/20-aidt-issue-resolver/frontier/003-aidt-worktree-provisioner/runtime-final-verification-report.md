# Frontier 003 runtime final verification

Date: 2026-07-21
Scope: final runtime, lazy facade, Binding Amendment 5 manifest repair, and frozen runtime contracts
Verdict: **FAIL / DO NOT APPROVE BUILD**

## Decision

The accepted eight runtime tests and 92 manifest tests are green, and the Amendment 5 repair is exact, but the final
runtime is not contract-complete. Independent hostile-value and capability probes found four required repair
clusters. Each is reachable through the public runtime surface and each contradicts an explicit frozen runtime rule.

No product or test file was changed during this verification. The next step is the smallest executable RED coverage
listed below, followed by separately assigned product repair.

## Required findings

### R1 — Public runtime DTOs are not total or semantically exact

Severity: MUST

`AidtWorktreeGeneration.__post_init__` computes an exact-settings flag but still dereferences every non-null settings
object (`runtime.py:82-94`). A hostile wrong type therefore escapes as Python `AttributeError` instead of the bounded
`AidtWorktreeFailure("internal_error")` required by the frozen DTO contract.

`AidtWorktreeHealth.__post_init__` tests status membership without first requiring exact `str`, so a `str` subclass is
accepted and an unhashable value leaks `TypeError` (`runtime.py:113-120`). Its timestamp validator proves only a regex
shape; it accepts impossible calendar values such as 31 February (`runtime.py:129-134`). This violates exact public
scalar types and the whole-second UTC timestamp invariant.

Fresh executable results:

```text
generation_hostile_settings AttributeError 'object' object has no attribute enabled
health_status_subclass_accepted Status
health_impossible_date_accepted 2026-02-31T01:02:03Z
health_unhashable_status TypeError cannot use 'list' as a set element (unhashable type: 'list')
```

Smallest RED first: extend `_assert_runtime_dtos_are_closed` inside the existing
`test_never_enabled_unmanaged_runtime_is_inert` test. Assert all four inputs raise exact bounded
`AidtWorktreeFailure("internal_error")`; retain the existing redacted generation-repr assertion. The product repair
should type-gate before dereference/membership and validate that the timestamp is a real UTC calendar instant, not
only a matching string.

### R2 — A successful prepare can be omitted from the action counter

Severity: MUST

Binding Amendment 4 requires the create/resume counter to increment immediately after a successful
`provisioner.prepare`, before the publication postcheck. `_record_success` instead reads the clock before acquiring
the lock or incrementing the action counter (`runtime.py:637-647`). If the clock becomes invalid after prepare has
completed, `create_or_reuse` returns fatal `clock_invalid` while the real successful prepare is not counted
(`runtime.py:314-323`).

Fresh executable result:

```text
post_prepare_clock owned_error clock_invalid 1 0 1 fatal
```

The fields are, in order: result disposition/category, prepare-call count, create count, failure count, and status.
The prepare ran once, but create remained zero.

Smallest RED first: add one bounded row to
`test_health_counts_create_resume_failure_and_sanitizes_last_detail`. Admit a valid provision action, make the clock
raise only after the fake prepare returns, then require `OWNED_ERROR("clock_invalid")`, one prepare call,
`create_count == 1`, `failure_count == 1`, and fatal status. The equivalent resume row may be parameterized without
adding a ninth public test name.

### R3 — Issued admission, guard, and cleanup-path capabilities are not enforced before delegation

Severity: MUST

The frozen map requires every admission field to match the current admitted attempt/route, every guard field to
match current ready evidence, and cleanup to receive the exact deterministic or durably recorded owned path.
Runtime currently checks only exact DTO type plus workflow generation for admission and guard
(`runtime.py:662-678`). Identifier-based removal establishes route ownership but does not compare the supplied path
before calling cleanup (`runtime.py:344-367,557-563`).

With the accepted fake provisioner, a valid current generation plus forged pair digest reaches both mutating
delegates, and a current identifier plus arbitrary absolute path reaches cleanup:

```text
forged_admission handled 1 ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
forged_guard handled 1 ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
forged_remove_path handled .../workspaces/WRONG
```

This is a runtime-boundary defect even though the production provisioner performs deeper proof: the frozen contract
explicitly assigns exact capability validation to runtime before delegation, and later integration is allowed to
trust only the runtime result.

Smallest RED first: add one helper under the existing
`test_delegate_converts_post_recognition_exceptions_to_owned_error` test:

1. replace only the issued admission pair digest and require owned `scope_changed` with zero prepare calls;
2. replace only the guard pair digest and require owned `scope_changed` with zero attest calls;
3. pass the current identifier with `workspace_root / "WRONG"` and require owned `path_invalid` with zero cleanup
   calls.

The product repair must validate the complete issued scope/revision/action and exact current or durable recorded path
without copying provisioner-private proof helpers.

### R4 — Runtime construction performs filesystem canonicalization I/O

Severity: MUST

The frozen constructor rule is canonicalization once without filesystem I/O. Runtime first performs lexical
normalization, but immediately calls public `stable_metadata_paths` (`runtime.py:212-220`). That helper calls
`_path_input`, whose canonicalizer invokes `Path.resolve(strict=False)` (`contract.py:278-291,411-426`). Resolving
symlinks is filesystem observation.

A `Path.resolve` sentinel placed after preparing an already absolute input fires during runtime construction:

```text
constructor_path_resolve AssertionError constructor resolve probe
```

Smallest RED first: in `test_never_enabled_unmanaged_runtime_is_inert`, prepare the absolute workflow path before
installing a `Path.resolve` raising sentinel, construct the runtime, and assert construction succeeds with no
sentinel call or metadata creation. The repair should keep one pure lexical canonical path and derive the stable
metadata DTO without a second filesystem-resolving pass.

## Recommended finding

### S1 — Revalidate retained consumed configuration before equivalent publication

Severity: SHOULD / hardening; explicitly outside this slice's ownership assumption

The private material key correctly snapshots the initially validated routing mapping, and closed profile validation
makes direct `_freeze` integer/string-key collision and NaN inputs unreachable through a valid publication.
Therefore those private-helper shapes are not a MUST defect.

However, mutating a consumed `aidt_routing.aidt_root` field on the published shallow `ServiceConfig.raw`, then
publishing a pristine equivalent config, returns the old DTO whose consumed raw value remains mutated:

```text
mutated_consumed_raw_retained True .../mutated-aidt
```

The implementation map states that Core owns the published config without mutation and that a deep immutable config
snapshot is outside this slice. Keep this as recommended follow-up: extend the immutable-key regression to a consumed
field and decide whether equivalent publication should revalidate the retained DTO or publish a fresh generation.

## Attack ledger

| Area | Result | Evidence |
|---|---|---|
| DTO totality, hostile types, repr | FAIL | R1; existing generation repr remains redacted |
| Health exact types/timestamp and no-I/O snapshot | FAIL/PASS | R1 fails constructor semantics; repeated snapshot itself remains memory-only |
| Constructor canonical path/no I/O | FAIL | R4 resolve sentinel |
| Immutable material key and mutable raw | PASS with recommendation | Closed validation blocks helper-only bad shapes; S1 retained mutation remains |
| Atomic publish/reject/fatal races and revision cap | PASS | publish lock plus install-under-lock; existing reload/fatal/race rows pass; cap blocks install above `MAX_INT` |
| Ownership before gates, root change, corrupt records | PASS | complete runtime ownership test passes; durable recognition precedes route/gate |
| Canonical loader `None` | PASS | exact canonical-child loader call returns unmanaged only for `None` |
| Attempt CAS and invalid-ready manual persistence under lock | PASS | runtime lock probe and 92-case manifest suite; Amendment 5 is exact `added/2` only |
| Forged admission/guard/path and generation identity | FAIL | R3; stale generation identity itself is correctly rejected by object identity |
| Action counter versus successful prepare | FAIL | R2 |
| Exception category/ref, fatal latch, no double count | PASS except R2 | existing exception/fatal rows pass; bounded details remain sanitized |
| Cleanup disposition | PASS/FAIL | preservation and owned-error counting pass; R3 path authority fails |
| Lazy facade, no private Git/parser/dynamic facade | PASS | all eight runtime tests, subprocess lazy check, and AST import boundary |
| Function length/nesting | PASS | 64 functions, maximum 34 lines and nesting 3 |

## Fresh verification evidence

| Gate | Exit | Result |
|---|---:|---|
| Complete runtime suite | 0 | `8 passed in 1.24s` |
| Complete manifest suite | 0 | `92 passed in 1.06s` |
| Independent hostile/capability probe | 0 | Reproduced all four MUST clusters and S1 exactly as quoted above |
| Ruff, owned runtime/facade/manifest/tests | 0 | `All checks passed!` |
| Pyright, same slice | 0 | `0 errors, 0 warnings, 0 informations` |
| Runtime AST | 0 | 64 functions; max 34 lines/nesting 3; no violations |
| No-index whitespace, owned product/tests | 1 | Expected content-difference exit; no output, so no whitespace finding |

The builder report records green `65` provisioner, `317` foundation, and `190` routing matrices. They were not
rerun after the independent semantic probe established a FAIL verdict; the parent explicitly stopped redundant long
matrices so the RED-test repair could begin. Those broad green results do not exercise the four missing runtime
contracts above.

## Scope and final verdict

This verification fully read the final runtime, complete facade, exact Amendment 5 validator/test, complete runtime
and manifest tests, PLAN Amendments 1-5, runtime implementation map, provisioner/runtime brief, integration brief,
and builder/prior verifier reports. It used no network, live repository, backend, Jira, Git mutation, or commit. The
platform agent-thread cap prevented another fresh child, so the parent assigned this independent verification to the
existing non-builder review thread.

**FAIL / DO NOT APPROVE BUILD.** Add the four bounded RED clusters before editing product code. R1-R4 are required;
S1 is recommended and remains outside the current config-ownership assumption.
