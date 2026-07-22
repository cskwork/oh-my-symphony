# Frontier 003 manifest-helper verifier report

Date: 2026-07-21

## Verdict

FAIL.

The six bounded helpers satisfy the repository function-size/nesting limits, and the existing focused manifest suite,
Ruff, and Pyright pass. The helper slice nevertheless has three MUST defects and one SHOULD defect: optional reads do
not enforce collision failure, valid backoff records self-invalidate as the clock advances and can regress durable
timestamps when it moves backward, and the phase/ready constructors accept impossible source states and arbitrary
manifest revisions. The focused tests do not exercise any of the six additions.

No product or test file was changed by this verifier.

## Findings

### MUST-1 - Optional reads bypass the required case-collision guard

Paths: `src/symphony/aidt_worktree/manifest.py:460`,
`src/symphony/aidt_worktree/manifest.py:483`,
`src/symphony/aidt_worktree/manifest.py:498`

Each helper performs only `_lexists(path)` followed by the exact reader. None scans the bounded parent registry for a
Unicode-normalized, case-folded sibling before returning `None` or accepting the exact file. On a case-sensitive
filesystem an alias can therefore coexist unnoticed; on a case-insensitive filesystem an alias can resolve as the
requested path and be accepted. The existing collision enforcement is confined to `_scan_records` at lines
1267-1288 and is never reached by these helpers. This contradicts the explicit foundation rule that collision remains
a failure.

Required correction: apply the same bounded NFC/case-fold collision rule before every optional result while
preserving `None` exclusively for a genuinely absent, collision-free exact record. Add case-sensitive and
host-case-folded fixtures for all three helpers.

### MUST-2 - Forward clock movement invalidates backoff transitions, while backward movement can regress time

Paths: `src/symphony/aidt_worktree/manifest.py:738`,
`src/symphony/aidt_worktree/manifest.py:764`,
`src/symphony/aidt_worktree/manifest.py:1359`,
`src/symphony/aidt_worktree/manifest.py:1486`

`initial_attempt_record` correctly creates a due attempt-zero record with `retry_at == updated_at == now`. On a later
tick, however, due admission preserves that old `retry_at` while advancing `updated_at`; `_bounded_retry_time` then
rejects the new record because it requires `updated_at <= retry_at`. A direct probe admitted the initial record at the
same second, but admission one second later raised `registry_invalid`, violating the binding forward-clock rule.

`advance_attempt_phase` repeats the same stale-retry construction. After same-second admission, `none -> prepared`
worked at second zero and raised `registry_invalid` one second later. Conversely, the helper accepts an injected UTC
time earlier than the source record's `updated_at` when it remains after `created_at`; a probe moved
`updated_at` backward from `00:00:10Z` to `00:00:05Z`. These outcomes are neither exact timestamp transitions nor the
required safe behavior under forward/backward wall-clock movement.

Required correction: keep due admission and every active backoff phase transition valid after elapsed time, retain
the bounded retry schedule, and prevent durable `updated_at` regression under a backward clock. Bind probes at the
same second, later seconds, the 600-second boundary, and a backward jump.

### MUST-3 - Phase and ready constructors accept impossible state/revision transitions

Paths: `src/symphony/aidt_worktree/manifest.py:764`,
`src/symphony/aidt_worktree/manifest.py:792`

The constructors check only adjacent phase text and whether `manifest_revision` is any positive revision. They do
not bind source disposition/category, a consumed attempt in the range 1-3, the source manifest revision, or the fixed
manifest state revisions. A direct probe changed a `manual`, attempt-zero record to `prepared` with manifest revision
99 and then to `ready`, still at attempt zero and revision 99. The same loose table allows `added -> removing` from a
non-ready record. This can erase the permanent manual disposition and fabricate ready/removing authority contrary to
the exact attempt state machine and preserve-only post-prepared failures.

Required correction: close each constructor over the exact allowed source shape and fixed manifest relationship:
provisioning transitions require a consumed bounded attempt, ready may only close the eligible prepared/added success
shape at ready manifest revision 2, and removing may only derive from the exact ready/added revision-2 shape into
revision 3. Reject manual, attempt-zero, already-ready, and arbitrary-revision inputs.

### SHOULD-1 - The focused suite never imports or calls the six additions

Path: `tests/test_aidt_worktree_manifest.py:25`

The import block and all 23 tests omit `read_optional_manifest`, `read_optional_ownership`,
`read_optional_attempt`, `initial_attempt_record`, `advance_attempt_phase`, and `ready_attempt_record`. Existing tests
therefore prove the underlying readers, persistence, and admission helpers, but provide no regression protection for
the newly authorized surface. The direct probes above reached defects while the focused suite remained green.

Required correction: after the MUST fixes, add behavioral tests for every allowed and rejected read/transition edge,
including collision, malformed/symlink/mode/type inputs, exact revisions, delayed/backward clocks, manual
preservation, and persistence through `persist_attempt` CAS only.

### NIT

None.

## Verified invariants

- All three optional helpers return `None` for an exact `ENOENT`; a dangling symlink remains a
  `registry_invalid`/failed read rather than absence. The underlying regular-file and canonical JSON readers retain
  the wrong-mode/type and malformed-byte guards.
- `initial_attempt_record` produces schema `aidt-worktree-attempt-v1`, record revision 1, category
  `attempt_backoff`, disposition `backoff`, attempt 0, phase `none`, null manifest revision, and whole-second UTC
  created/updated/retry timestamps when given valid scope input.
- The constructors return records only. Durable writes remain centralized in `persist_attempt`, whose existing
  revision increment, created-time immutability, canonical encoding, exclusive temporary file, file fsync, replace,
  and directory-fsync path is unchanged.
- All six helpers are under 50 lines and nesting 4: measured source lengths are 5, 5, 5, 24, 26, and 24 lines; the
  maximum measured nesting is 1.

## Verification evidence

| Gate | Exact result |
|---|---|
| Focused `tests/test_aidt_worktree_manifest.py` | 23 passed in 0.30s |
| Ruff over focused product/test, cache disabled | `All checks passed!` |
| Pyright over focused product/test | 0 errors, 0 warnings, 0 informations |
| Independent AST gate over the six helpers | maximum 26 lines; maximum nesting 1 |
| Fresh clock/state semantic probe | reproduced later-tick `registry_invalid`, delayed-phase `registry_invalid`, timestamp regression, and manual attempt-zero revision-99 promotion to ready |
| Fresh optional-read probe | exact absence returned `None`; dangling symlink failed as `registry_invalid` |

## Scope and audit notes

Read: repository `AGENTS.md`; binding PLAN amendments I, O, P, and Amendment 3; the manifest-helper section of
`provisioner-test-brief.md`; `provisioner-builder-paused-handoff.md`; current
`src/symphony/aidt_worktree/manifest.py`; and current `tests/test_aidt_worktree_manifest.py`. The paused provisioner
draft was consulted only to confirm the intended helper call sequence; it was not audited or executed as acceptance
evidence.

This verifier wrote only this report through `apply_patch`. No product/test edit, live Git operation, network access,
commit, branch/ref mutation, or activation occurred.
