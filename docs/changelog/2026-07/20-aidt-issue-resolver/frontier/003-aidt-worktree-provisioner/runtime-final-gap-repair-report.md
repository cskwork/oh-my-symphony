# Frontier 003 runtime final-gap repair report

Date: 2026-07-21
Scope: final verifier R1-R4 runtime repair
Verdict: **GREEN / READY FOR FINAL INDEPENDENT VERIFICATION**

## Decision

The four final-verifier MUST clusters are repaired in `src/symphony/aidt_worktree/runtime.py` only. Runtime DTOs now
fail closed for hostile public values, successful provisioner actions are counted before any post-prepare clock read,
admissions and guards are runtime-issued identity capabilities, cleanup paths are bound to exact owned paths, and
constructor metadata derivation is purely lexical.

No test, facade, manifest, contract, provisioner, routing, workspace, Core, entry, or schema product file was changed
for this repair.

## Root-cause repairs

### R1 — total, exact public DTOs

- Generation validates exact settings type before dereferencing it and requires an exact string generation only for
  an enabled exact settings DTO.
- Health validates exact status type before membership, exact bounded category/ref scalars, and a real calendar
  instant through strict UTC parsing after the closed timestamp shape check.
- Wrong settings, string subclasses, unhashable status values, and impossible dates all close as
  `AidtWorktreeFailure("internal_error")` without leaking Python implementation exceptions.

### R2 — successful action accounting before clock

- Admission consumption and successful prepare completion increment `admission.action` under the runtime lock before
  reading or formatting the clock.
- A later invalid clock records one fatal `clock_invalid` failure while preserving the real create/resume count.
- A malformed provisioner result retains the returned action count, issues no guard, and maps to `internal_error`;
  publication races retain the count but map to `scope_changed`.

### R3 — issued capabilities and exact cleanup path

- Handled admissions are issued only while the exact generation remains current, open, and nonfatal.
- Admission consume performs get, exact generation/object-identity validation, and pop atomically. A forged lookalike
  neither executes nor revokes the genuine one-use capability.
- Successful, internally aligned prepare results issue their exact guard only while the same generation is still
  current. Guards are reusable but only by exact object identity.
- Capability maps are keyed by canonical identifier, so newer same-child capabilities replace older ones and memory
  remains bounded by recognized identifiers. Material install, rejected reload, and fatal latch clear both maps.
- Identifier cleanup accepts the durable recorded path exclusively when exact manifest/owner evidence exists. It
  accepts the current deterministic path only when both path records are absent; one-sided evidence stays
  `registry_invalid`, and a mismatched path is `path_invalid` before cleanup.

### R4 — constructor metadata without filesystem observation

- Constructor path normalization remains lexical (`is_absolute` plus `normpath`).
- Stable workflow identity and metadata paths are derived directly from the normalized bytes and frozen domain hash;
  construction no longer calls `stable_metadata_paths` or `Path.resolve` and creates/probes no metadata.

## Verification evidence

All pytest commands used `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src`, and no cache provider.

| Gate | Result |
|---|---|
| Frozen final-gap runtime suite | `8 passed in 1.02s` |
| Contract + manifest + lazy facade controls | `125 passed in 1.65s` |
| Complete provisioner suite | `65 passed in 306.35s` |
| Route dispatch + routing contract/decision/storage/runtime/Git-object compatibility | `204 passed in 26.23s` |
| Ruff on owned product/test slice | `All checks passed!` |
| Pyright on owned product/test slice | `0 errors, 0 warnings, 0 informations` |
| Runtime product AST | 74 functions; max 34 lines; max nesting 3; no violations |
| Lazy/default-off and forbidden-import boundary | Covered by the green runtime subprocess/AST test |
| Tracked whitespace | `git diff --check` exit 0 with no findings |
| No-index runtime/report whitespace | Expected content-difference exit 1 with no findings |

During the first replay, an old test helper expected an unissued synthetic guard after reload recovery. That
contradicted the newly frozen exact-issued-guard rule; the test owner corrected the helper to obtain a guard through
admit/prepare. Product retained the fail-closed capability rule and added no bypass.

No network, live repository, Jira, backend, AIDT checkout, Git mutation, or commit was used.

## Final ownership-pair repair

Final re-verification found one remaining removal-finality gap: an unknown explicit identifier returned `UNMANAGED`
before reverse-checking a path already owned by another durable AIDT child.

`_recognize_removal` now handles only the explicit-identifier `UNMANAGED` branch through one bounded reverse check:

- `registry_recognizes_path` false preserves exact `UNMANAGED`;
- a durably owned path returns `OWNED_ERROR("path_invalid")` with the bounded supplied identifier ref;
- bounded registry/path failures remain owned errors through the existing exception mapper;
- recognized identifiers continue through exact current/durable pair validation and gate ordering;
- no cleanup call or filesystem operation occurs under the runtime lock.

Final ownership repair evidence:

| Gate | Result |
|---|---|
| Frozen runtime suite with unknown/recognized cross-child controls | `8 passed in 0.96s` |
| Contract + manifest + lazy facade controls | `125 passed in 1.55s` |
| Ruff on runtime and runtime tests | `All checks passed!` |
| Pyright on runtime and runtime tests | `0 errors, 0 warnings, 0 informations` |
| Runtime product AST | 75 functions; max 34 lines; max nesting 3; no violations |

The earlier complete provisioner `65` and route/routing `204` green matrices were not repeated because this final
slice changes recognition only and performs no delegation when it fires.
