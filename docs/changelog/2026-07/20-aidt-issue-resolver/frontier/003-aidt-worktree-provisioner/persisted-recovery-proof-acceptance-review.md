# Frontier 003 persisted recovery-proof API acceptance review

Date: 2026-07-21

## Verdict

FAIL.

The documented public surface, ordinary quiescent-state behavior, lazy exports, bounded command set, and standard
failure categories pass. The API nevertheless has two MUST implementation defects and one MUST coverage defect. A
proof can describe collections or ticket cleanliness that are no longer true when the function returns, and the
public frozen result DTOs do not totalize all malformed state as `protocol_invalid`. The focused suite does not bind
either seam and omits multiple rows from the explicitly exhaustive acceptance matrix.

No source or test file was changed by this reviewer.

## Findings

### MUST-1 - The proof is not consistent at return

Paths: `src/symphony/aidt_worktree/git_state.py:482`,
`src/symphony/aidt_worktree/git_state.py:552`,
`src/symphony/aidt_worktree/git_state.py:598`,
`src/symphony/aidt_worktree/git_state.py:1165`,
`src/symphony/aidt_worktree/git_state.py:1277`

All three operations collect refs and worktree registrations once through `_observe_recovery_collections`, then run
additional identity, ticket, ancestry, root-content, and path observations before returning. There is no closing
collection observation. Ready/cleanup observes ticket status only once, and path presence/absence is also not
revalidated after the remaining commands. The caller-held common-Git lock does not serialize arbitrary Git commands
or ticket filesystem writes from other processes.

Two injected-runner temporary-repository probes returned success across those gaps:

```text
prepared:
proof_returned=PreparedRecoveryProof
proof_phase=s1
unrelated_in_returned_state=False
unrelated_in_repository_at_return=True

cleanup entry:
proof_returned=ReadyRecoveryProof
proof_phase=cleanup_pre
proof_ticket_clean=True
repository_ticket_clean_at_return=False
```

The first probe captured `for-each-ref`, then added `refs/heads/fix/A20-9999` before the remaining proof commands.
The returned S1 still matched the persisted digest because it used the captured collection. The second probe wrote an
untracked ticket file immediately after the ticket `status` command. The registered ticket is excluded from the root
content proof, so cleanup returned `clean=True` while the worktree was dirty. This contradicts the required genuinely
current state (`persisted-recovery-proof-api.md:72`) and the cleanup equality check immediately before removing intent
(`persisted-recovery-proof-api.md:183`).

Required correction: bracket every proof with bounded closing observations and require the identity, refs,
registrations, target path shape, and applicable ticket observation to remain equal. Build/return only the bracketed
state, failing closed on any difference. For example:

```python
before_refs, before_regs = _observe_recovery_collections(identity, runner)
# target, ticket, ancestry, root and projection checks
after_refs, after_regs = _observe_recovery_collections(identity, runner)
if (after_refs, after_regs) != (before_refs, before_regs):
    raise AidtWorktreeFailure("identity_invalid")
```

The real implementation must also repeat the no-follow path witness and, for complete targets, ticket
HEAD/branch/status/upstream. Add mutation seams after the initial collection and after the initial ticket/path check
for prepared, resume, cleanup-pre, and removed recovery.

### MUST-2 - Result DTO validation accepts malformed fields and leaks raw exceptions

Paths: `src/symphony/aidt_worktree/git_state.py:182`,
`src/symphony/aidt_worktree/git_state.py:1365`,
`src/symphony/aidt_worktree/git_state.py:1398`

`_valid_result_state_type` checks only the exact `RepositoryState` and `RepositorySnapshot` classes. The subsequent
validators iterate `state.refs` and `state.registrations` without first validating their types or member types.
`_valid_result_ticket` never validates `status_digest`.

Fresh public-constructor probes demonstrated both outcomes:

```text
PreparedRecoveryProof(valid_state, replace(ticket, status_digest=[]), digest)
accepted_dto=PreparedRecoveryProof
status_digest_type=list

PreparedRecoveryProof(replace(valid_state, refs=None), valid_ticket, digest)
exception_type=TypeError
exception_text='NoneType' object is not iterable
```

The first object is a frozen public DTO containing a value outside its declared and canonical shape. The second
escapes the documented fail-closed category instead of raising `AidtWorktreeFailure("protocol_invalid")`. This does
not satisfy the result-shape invariant requirement at `persisted-recovery-proof-api.md:254`.

Required correction: validate all `RepositoryState` collection/container/member, branch, absolute-path, upstream,
and snapshot field shapes before traversing them. Validate `TicketWorktreeState.status_digest` as lowercase 64-hex
and bind `clean` to the canonical empty-status digest. Every malformed public result-constructor input must totalize
to `protocol_invalid`, for example:

```python
collections_valid = (
    type(state.refs) is tuple
    and all(type(item) is RefRecord for item in state.refs)
    and type(state.registrations) is tuple
    and all(type(item) is WorktreeRegistration for item in state.registrations)
)
```

Add field-by-field malformed DTO tests, including wrong container/member types, invalid/relative target fields,
invalid status digests, and clean/status-digest disagreement.

### MUST-3 - The test suite is not the specified exhaustive matrix

Path: `tests/test_aidt_worktree_recovery_proofs.py:394`,
`tests/test_aidt_worktree_recovery_proofs.py:494`,
`tests/test_aidt_worktree_recovery_proofs.py:583`,
`tests/test_aidt_worktree_recovery_proofs.py:781`,
`tests/test_aidt_worktree_recovery_proofs.py:850`

The 92 cases are useful and pass, but only this file references the recovery API, and it contains no inter-observation
collection/path/ticket race. Its ready/cleanup rejection matrix also omits the explicitly required root HEAD and
symbolic drift, same-status ignored-content mutation, protected and unrelated-registration drift, target path and
registration/head mismatch, detached registration, and prunable registration rows. Removed recovery omits the
registration-only/mismatched-registration, protected/unrelated-registration, root HEAD/symbolic, and already-complete
persisted phase rows. Projection/tamper coverage does not exercise every snapshot digest/count/target field or the
documented boundary and boundary-plus-one counts. The DTO matrix omits the malformed fields demonstrated in MUST-2.

These are required by the exhaustive matrix beginning at `persisted-recovery-proof-api.md:227`, especially ready
recovery at line 237, removed recovery at line 244, projection properties at line 251, and API/result safety at line
254. Green coverage cannot establish acceptance while these rows and the reproduced races are absent.

Required correction: add the omitted real temporary-Git rows and deterministic injected-runner race seams. Require
each to fail with the frozen category, then retain the current positive cases and no-mutation spy.

### SHOULD

None beyond the required corrections above.

### NIT

None.

## Verified behavior

- Public signatures and parameter order match the frozen design. `runner` is keyword-only with default `None`, and
  ready `phase` is required and keyword-only.
- The facade exports all three DTOs and all three operations lazily. A fresh process does not import `git_state`
  until first attribute access.
- Quiescent temporary repositories return the exact absent/completed/ready/cleanup/removed DTO shapes. Independently
  computed create/remove digests equal `validate_create_delta` and `validate_remove_delta`.
- Nine ordinary invalid-input cases returned the documented categories: wrong snapshot type, binding mismatch,
  invalid branch, relative path, malformed observed time, invalid ready phase, malformed retained SHA, premature
  removed proof, and malformed top-level DTO state.
- A command/fingerprint spy observed only `rev-parse`, `remote get-url`, `worktree list`, `for-each-ref`, `status`,
  `symbolic-ref`, and `merge-base`. It saw zero fetch/add/remove/checkout/reset/rebase/prune/clone/pull/push/ls-remote
  operations, and quiescent before/after repository fingerprints were equal.
- All 139 functions in `git_state.py` satisfy the structural gate: maximum 42 physical lines and maximum nesting 4.
- Product Pyright is clean. The explicit product-plus-focused-test invocation reports one test-only argument-type
  error at `tests/test_aidt_worktree_recovery_proofs.py:774`; this does not cause the runtime defects above but means
  that broader invocation is not a clean gate.

## Verification evidence

| Gate | Exact result |
|---|---|
| Focused recovery proofs | `92 passed, 1 warning in 82.33s`; exit 0 |
| Existing Git-state + manifest regression | `209 passed, 1 warning in 47.05s`; exit 0 |
| Independent delegated focused rerun | `92 passed, 1 warning in 84.48s`; exit 0 |
| Product Pyright | `0 errors, 0 warnings, 0 informations`; exit 0 |
| Product + focused-test Pyright | `1 error, 0 warnings, 0 informations`; exit 1 at test line 774 |
| AST structural gate | `functions=139 max_lines=42 max_nesting=4` |
| No-index whitespace checks over product/facade/test/report | expected exit 1 for content difference; zero output |
| Prepared collection-race probe | returned S1 while the repository had one newly added unrelated ref |
| Cleanup ticket-race probe | returned `ticket.clean=True` while ticket status was non-empty |
| DTO status-digest probe | accepted `status_digest=[]` |
| DTO collection-totalization probe | leaked raw `TypeError: 'NoneType' object is not iterable` |
| Quiescent no-side-effect probe | fingerprints unchanged; 0 mutation/network commands across all proof shapes |

The pytest warning is environmental: the installed pytest does not recognize the repository's `asyncio_mode`
configuration. The focused tests are synchronous and all selected cases executed.

## Scope and audit notes

Read completely: applicable worktree/repository `AGENTS.md`, ancestor `CLAUDE.md`,
`/Users/chaeseong-gug/.codex/RTK.md`, `WORKFLOW.md`, both available host-board cards, the frozen API, builder report,
product implementation, facade, and focused tests. The worktree has no closer `CLAUDE.md`; the applicable ancestor
file is `/Users/chaeseong-gug/Documents/PARA/Project/Git/CLAUDE.md`.

All dynamic checks used disposable local SHA-1 repositories with the canonical fixture HTTPS origin. No network,
external service, live/user repository, commit, branch/ref mutation outside temporary fixtures, source edit, test
edit, or product-file mutation occurred. The three reviewed product/test paths were already untracked in the shared
worktree; the reviewer wrote only this report. The delegated probe artefacts are under `/tmp` and are not durable
repository fixtures.
