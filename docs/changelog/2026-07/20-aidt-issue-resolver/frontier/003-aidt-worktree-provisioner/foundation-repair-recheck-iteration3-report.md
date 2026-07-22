# Frontier 003 foundation repair recheck iteration 3

Date: 2026-07-21

## Verdict

FAIL.

One MUST remains; no SHOULD remains. The nested-remote, attempt-state, hooks-root, and optional-reader repairs pass
their targeted probes. The type-2 porcelain repair is incomplete: real Git 2.53.0 emits valid worktree-side rename and
copy rows with XY `.R` and `.C`, while the public parser accepts only index-side `R.` and `C.`. Fresh isolated fixtures
emitted `.R R100` and `.C C75`; both raised `AidtWorktreeFailure("content_invalid")`.

The current 174-test focused suite and 410-test nine-suite superset still pass because the regressions exercise only
index-side rename/copy rows. Ruff, product Pyright, AST, lazy-facade, and whitespace gates are clean. Those gates do
not supersede the real-Git grammar failure.

No product or test file was changed by this verifier.

## Finding

### MUST-1 - Valid worktree-side rename and copy records are rejected

Paths: `src/symphony/aidt_worktree/git_state.py:1565-1572`,
`src/symphony/aidt_worktree/git_state.py:1603-1604`

`_valid_rename_xy` currently requires `xy[0] in b"RC"` and `xy[1] in b".MTD"`. `_rename_status` likewise couples
the `R|C` score marker only to `fields[1][:1]`. This accepts the valid index-side matrix but rejects the other valid
type-2 form: unchanged index plus worktree-side rename/copy.

Fresh isolated Git 2.53.0 evidence:

```text
fixture                                      Git type-2 fields  public parser
committed source moved + target intent-add   .R R100            content_invalid
75%-similar copy + changed source/intent-add .C C75             content_invalid
```

The `.R` fixture used `status.renames=true`; the `.C` fixture used `status.renames=copies`. Both came directly from
`git status --porcelain=v2 -z --untracked-files=all`, not synthesized bytes. No repository outside an isolated
temporary directory was used.

The prior recheck already stated the binding matrix as “index rename/copy with worktree `.MTD`, or unchanged index
with worktree `R/C`.” The iteration-2 repair report instead calls the index-only predicate exact. That is a new report-
to-code contradiction, and the tests at `tests/test_aidt_worktree_git_state.py:294-397` reproduce it: every accepted
type-2 row is built as `R.` or `C.`.

The rest of the score/XY repair is correct. A real staged rename emits and accepts `R99`; synthetic non-padded
`R0`, `R9`, `R99`, `R100`, and index-side `C75` pass; zero-padded, negative, missing-kind, and over-100 scores fail;
score-prefix/XY mismatch fails; impossible `MR` and `RR` fail.

Required correction: accept exactly `(R|C)[.MTD]` or `.(R|C)`, derive the active rename/copy byte from the side that
contains it, and require the score prefix to equal that byte. Add real `.R R100` and `.C C75` regressions plus
synthetic marker-coupling cases for both sides. Retain rejection of `MR`, `RR`, two-sided `RC|CR`, and every other
command-impossible pair.

## Recheck matrix

| Prior finding | Result | Fresh evidence |
|---|---|---|
| MUST: nested multi-component remote feature collision | PASS | `team/origin/fix/A20-1188` is `AMBIGUOUS` and snapshot raises `collision`; `fix/A20-11880` and `prefix-fix/A20-1188` remain unrelated |
| MUST: type-2 score bounds and XY coupling | FAIL | Index-side `R99`/`C75`, bounds, `MR`, and `RR` behave correctly; real `.R R100` and `.C C75` are rejected (MUST-1) |
| MUST: canonical attempt category/disposition/phase/clock | PASS | 41-case targeted matrix; forged permanent-category backoff rows reject; waiting and consumed retry shapes remain distinct |
| SHOULD: hooks-root before-open/during-scan identity | PASS | Descriptor-bound `O_DIRECTORY|O_NOFOLLOW`, `lstat`/`fstat`, descriptor scan, and post-scan identity regressions pass |
| SHOULD: optional reader cap-plus-one/no-overread | PASS | Manifest, ownership, and attempt readers each stop after the 2,501st witness and close the iterator |

## Passed invariant details

### Nested remote collision

`_has_remote_target_ref` accepts an arbitrary nonempty remote-name prefix before the exact derived branch suffix.
Both public classification and repository observation reject `refs/remotes/team/origin/fix/A20-1188`. Exact suffix
matching does not overreach to `refs/remotes/team/origin/fix/A20-11880` or
`refs/remotes/team/origin/prefix-fix/A20-1188`.

### Attempt state machine

The durable validator now couples all attempt dimensions:

- the only backoff categories are `attempt_backoff`, `scope_changed`, `lock_timeout`, `fetch_timeout`, and
  `fetch_command_failed`;
- attempt zero is limited to initial/scope-reset, phase `none`, null manifest revision, and
  `retry_at == updated_at`;
- future transient retries at phase `none` remain valid waiting records but cannot call a phase helper;
- due admission consumes a new revision/attempt and writes `retry_at == updated_at`; prepared/added active states
  require that consumed clock shape and manifest revision 1;
- every permanent category is manual-only, `attempt_exhausted` requires attempt 3, and ready/removing use only the
  exact `ready` category/disposition and revisions 2/3.

The constructor matrix, all permanent-category forged canonical reads, exact CAS persistence, elapsed/backward clock,
waiting-versus-consumed, and active-source allowlist tests pass. No category/disposition/phase/clock contradiction was
found in the repaired code.

### Hooks root

The hooks root is pathname-`lstat`ed, opened with `O_DIRECTORY|O_NOFOLLOW`, matched to descriptor `fstat`, enumerated
through that descriptor, and matched to a final pathname `lstat` before acceptance. Replacement with a symlink both
immediately before descriptor open and during enumeration fails before mutation. Entry stats remain no-follow, and
the entry loop rejects at cap-plus-one without materializing or statting beyond the bound.

### Optional readers

All three optional readers share `_optional_collision_matches`. It retains only names with the exact NFC/case-fold
collision key, raises as soon as enumeration yields entry 2,501, requests no 2,502nd entry, and closes the scan in a
`finally` block. Exact collision-free ENOENT alone returns `None`; aliases and malformed existing records still fail.

## Verification evidence

| Gate | Exact result |
|---|---|
| Targeted Git repair matrix | `31 passed, 52 deselected in 7.56s`; incomplete because no `.R`/`.C` fixture |
| Targeted manifest repair matrix | `41 passed, 50 deselected in 0.33s` |
| Fresh real-Git worktree rename probe | emitted `.R R100`; parser returned `content_invalid` |
| Fresh real-Git worktree copy probe | emitted `.C C75`; parser returned `content_invalid` |
| Focused Git-state + manifest suites | `174 passed in 30.62s` |
| Current nine-suite pre-provisioner superset | `410 passed in 80.85s` |
| Ruff `--no-cache` over Git-state, manifest, facade, and focused tests | `All checks passed!` |
| Product Pyright over Git-state, manifest, and facade | `0 errors, 0 warnings, 0 informations` |
| Existing executable AST/lazy tests | `2 passed in 0.25s` |
| Independent product AST scan | Git-state 113 functions, max 39 lines/nesting 4; manifest 101 functions, max 34 lines/nesting 3 |
| Fresh lazy-facade processes | cold `False/False`; Git access `True/False`; manifest access `False/True` |
| Tracked whitespace | `git diff --check` exit 0, no output |
| Five owned untracked file checks | expected no-index exit 1; zero whitespace output each |

The nine-suite command covered routing contract, storage, decision, Git objects, runtime, route dispatch, worktree
contract, manifest, and Git-state. It excludes the intentionally paused provisioner/runtime draft.

## Scope and audit notes

Read: the updated foundation recheck, both iteration-2 repair reports, current Git-state/manifest/facade product code,
both focused test files, and the relevant durable category set. Every named prior finding was mapped to current code
and rerun. An independent delegated Git audit found the worktree-side XY omission; the main verifier reproduced both
real-Git failures separately and reviewed the remaining areas.

This verifier wrote only this report through `apply_patch`. It made no product/test edit, network request, live
repository/AIDT operation, commit, branch/ref mutation, or activation. Git grammar checks used only installed Git
2.53.0 and disposable temporary repositories.
