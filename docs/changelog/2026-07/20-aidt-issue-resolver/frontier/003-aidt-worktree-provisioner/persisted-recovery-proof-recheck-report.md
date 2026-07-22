# Persisted recovery-proof repair recheck

Date: 2026-07-21

## Verdict

PASS.

Zero MUST and zero SHOULD findings remain in the persisted recovery-proof repair. The three prior acceptance blockers
are closed on the current bytes: every proof phase has a closing identity/ref/registration and no-follow path bracket,
complete-target phases repeat the ticket witness; all public proof DTOs totalize malformed nested state to
`protocol_invalid` while binding snapshots to raw collections; and the focused suite now exercises the documented
race, drift, projection, tamper, boundary, and malformed-DTO matrix.

This is a narrow recovery-proof gate. The intentionally paused provisioner/runtime draft remains deferred and was not
treated as implemented, activated, repaired, or accepted by this review.

## Prior required corrections

### 1. Return-state bracketing across every phase - PASS

The public operations at `src/symphony/aidt_worktree/git_state.py:464-630` all start with
`_observe_recovery_collections`. `_close_recovery_bracket` at lines 1210-1248 then:

- re-observes repository identity, worktree registrations, and refs;
- repeats the no-follow target-path shape after the closing collection commands;
- requires the closing collections to equal the initial bounded tuples;
- for complete targets, repeats ticket top-level identity, HEAD, symbolic branch, porcelain status digest,
  cleanliness, and upstream absence, then repeats the no-follow path witness once more;
- returns the equal closing ticket observation while the returned `RepositoryState` retains only initial raw
  collections proven equal to the closing collections.

Prepared absence and removed absence close without a ticket; prepared completed-add, ready resume, and cleanup entry
close with the repeated ticket witness. Collection changes fail as `identity_invalid`, path changes as `collision`,
and ticket-content changes as `content_invalid`.

Fresh focused execution covered the initial collection/path/ticket seams and path mutation during the closing
collection commands at `tests/test_aidt_worktree_recovery_proofs.py:317-515`. All rows passed.

### 2. Public DTO totalization and snapshot/raw consistency - PASS

`_valid_result_state_type` and its helpers at `src/symphony/aidt_worktree/git_state.py:1425-1604` validate before
traversal and catch malformed nested values. The checks require:

- exact `RepositoryState`, `RepositorySnapshot`, tuple, `RefRecord`, `WorktreeRegistration`, and ticket DTO types;
- bounded and unique refs/registrations; canonical branch/ref/SHA/bool grammar and absolute paths;
- a successfully reconstructed `RepositorySnapshot` with the unchanged 18-field schema;
- raw ref/registry/protected digests and counts, fixed base, target ref/registration, and upstream to equal the
  snapshot/scalar values;
- lowercase 64-hex ticket status, exact ticket path/branch/HEAD, upstream absence, and
  `clean == (status_digest == canonical_empty_digest)`.

Every public `__post_init__` converts a failed shape to `AidtWorktreeFailure("protocol_invalid")`; no raw
`TypeError`/`AttributeError` path remained. The malformed-state, nested collection/member, forged snapshot/raw,
ticket-field, and cross-field matrices at test lines 517-769 and 1462-1508 passed.

### 3. Documented acceptance matrix - PASS

The 153-case temporary-Git suite closes the rows identified by the prior FAIL:

- prepared: exact absence and completed clean add; every no-follow path artifact; branch/registration/remote/mixed
  target shapes; wrong SHA/upstream/flags; tracked, untracked, and ignored dirt; fixed/root/protected/unrelated drift;
- ready resume and cleanup entry: clean/dirty/descendant positives as allowed; root HEAD/symbolic/status/content and
  same-status ignored-content drift; fixed/protected/unrelated ref and registration drift; path/branch/upstream/flag
  mismatch; explicit ref/registration/ticket HEAD disagreement; remote collision and non-descendant movement;
- cleanup-only cleanliness: tracked, untracked, and ignored dirty states all reject;
- removed: retained branch plus absent path/registration succeeds; remaining or mismatched registration, branch
  absence/movement/upstream, every path artifact, remote/fixed/root/protected/unrelated drift, retained-SHA mismatch,
  half-null/wrong/already-complete persisted phases reject;
- projection and persistence: unrelated ref/registration add/delete/change/rename reject; every snapshot digest/count
  family, base/binding/target shape, 10,000/10,001, 512 MiB/plus-one, and 2,500/2,501 boundaries fail closed when not
  observed;
- API/safety: result invariants, runner cap propagation, lazy facade, observed-time projection, no-mutation spy,
  public-slice boundaries, and genuine create/remove delta equality are exercised.

The provisioner-only ordering, CAS, authority, `_state_from_snapshot` deletion, and fresh-authority remove-retry rows
remain in the explicitly paused provisioner slice documented by `provisioner-builder-paused-handoff.md`. They are not
claimed by this API repair and were not pulled forward or silently accepted here.

## Public surface, safety, and quality

- Public signatures exactly match `persisted-recovery-proof-api.md`, including parameter order, keyword-only
  `runner`, and required keyword-only ready `phase`.
- The lazy facade contains all six recovery exports in `TYPE_CHECKING`, `_GIT_STATE_EXPORTS`, and `__all__`. A fresh
  process reported `git_state=False, manifest=False` before access and `git_state=True, manifest=False` after Git
  recovery access.
- `RepositorySnapshot` retains the existing 18 fields and target/phase shape validation; no schema or golden-byte
  change occurred.
- An independent disposable SHA-1 repository command/fingerprint probe exercised prepared absence, prepared
  completed-add, ready resume, cleanup entry, and removed recovery. Every before/after fingerprint was equal. The
  complete observed command set was limited to `rev-parse`, `remote get-url`, `worktree list`, `for-each-ref`,
  `status`, `symbolic-ref`, and `merge-base`; the probe rejected fetch/add/remove/checkout/reset/rebase/prune and a
  broader mutation/network set.
- All 149 product functions remain within the repository cohesion gate: maximum 47 physical lines and maximum
  control nesting 4. The recovery helpers separate observation, projection, invariant comparison, bracket closure,
  and result validation without exposing a generic raw-collection digest API.
- Security/performance review found no secret, injection, authorization, network, unbounded loop, or mutation-path
  addition. Counts, byte caps, parser bounds, no-follow checks, and fail-closed categories remain in force.

## Verification evidence

| Gate | Fresh result |
|---|---|
| Focused recovery proofs | `153 passed, 2 warnings in 202.05s (0:03:22)` |
| Git-state + manifest foundation | `209 passed, 1 warning in 23.40s` |
| Executable AST + lazy facade subset | `2 passed, 116 deselected in 0.23s` |
| Product + recovery-test Pyright | `0 errors, 0 warnings, 0 informations` |
| Ruff `--no-cache` over product, facade, recovery tests | `All checks passed!` |
| Independent AST scan | `functions=149 max_lines=47 max_nesting=4` |
| Independent five-shape command/fingerprint probe | all five `unchanged=True`; observation-only command set above |
| Untracked product/facade/test whitespace | three no-index `--check` runs; expected content-difference status, zero output |

The pytest warnings were environmental only: system pytest did not recognize the repository's `asyncio_mode`, and
the focused invocation could not create `.pytest_cache` in this read-only worktree. The synchronous 153 cases all
executed and passed; the foundation rerun disabled the cache provider.

## Review categories

### MUST

None.

### SHOULD

None.

### NIT

None.

### Positive feedback

The repair keeps the public API and durable schema frozen while centralizing the race bracket and DTO consistency
checks into bounded, cohesive helpers. The expanded tests are behavior-oriented disposable Git fixtures and retain a
strict no-mutation spy.

### Questions for the author

None.

## Scope and safety

Read completely: applicable worktree `AGENTS.md`, ancestor `CLAUDE.md`, `/Users/chaeseong-gug/.codex/RTK.md`,
`WORKFLOW.md`, both available host-board cards, the frozen recovery API, prior acceptance review, repair report,
post-reflection foundation recheck, current `git_state.py`, lazy facade, recovery test file, relevant manifest DTOs
and validators, and the paused provisioner handoff. The worktree has no closer `CLAUDE.md`.

All dynamic checks used ordinary local unit tests or disposable repositories under `/private/tmp`. No network,
external service, Jira, live/user repository, product/test edit, provisioner/runtime/schema edit, branch/tag/commit,
or activation occurred. This report is the only file written by the recheck.
