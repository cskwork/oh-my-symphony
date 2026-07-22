# Post-reflection foundation recheck

Date: 2026-07-21

## Verdict

PASS.

Zero MUST and zero SHOULD findings remain. The bounded post-reflection correction satisfies every forced-reflection
stop condition: all ten real Git type-2 families parse with exact side/kind coupling, both compound changes remain
separate producer records, all 24 mismatch/mixed negatives fail closed, and every prior foundation finding remains
green. The focused, 445-test compatibility, lint, type, structure, lazy-import, and whitespace gates all pass.

This is a hard-stop gate result, not a product mutation or lifecycle action. No product or test file was changed by
this verifier.

## Spec compliance

The authorized product change is exact and minimal:

- `_rename_status` keeps the existing score grammar and compares its `R|C` prefix with the kind derived from XY;
- `_valid_rename_xy` delegates only to the new `_rename_kind` helper;
- `_rename_kind` accepts exactly `(R|C)[.MTD]` or `.(R|C)` and returns the sole active kind;
- common submodule, mode, object-ID, NUL framing, path, duplicate, and cap validation is unchanged.

Relative to the implementation quoted by the iteration-3 FAIL, the product delta is confined to
`src/symphony/aidt_worktree/git_state.py:1565-1616`. The accompanying test delta adds disposable real-Git producers,
the ten-family matrix, two compound controls, and symmetric negative cases. Manifest behavior predates and is
unchanged by this correction.

The target files are accumulated untracked Frontier work, so Git cannot attribute a standalone per-loop diff. Direct
comparison with the iteration-3 captured implementation, current code locality, test locality, correction report,
and an independent delegated scope audit all agree on the three-file correction claim. No unauthorized parser,
mutation, facade, manifest, or runtime behavior was found.

## Real Git producer matrix

Fresh isolated Git 2.53.0 repositories produced and the public parser accepted:

| Family | Exact producer XY | Exact score | Result |
|---|---|---|---|
| index rename, unchanged worktree | `R.` | `R100` | PASS |
| index rename, modified worktree | `RM` | `R100` | PASS |
| index rename, type-changed worktree | `RT` | `R100` | PASS |
| index rename, deleted worktree | `RD` | `R100` | PASS |
| index copy, unchanged worktree | `C.` | `C75` | PASS |
| index copy, modified worktree | `CM` | `C75` | PASS |
| index copy, type-changed worktree | `CT` | `C75` | PASS |
| index copy, deleted worktree | `CD` | `C75` | PASS |
| unchanged index, worktree rename | `.R` | `R100` | PASS |
| unchanged index, worktree copy | `.C` | `C75` | PASS |

The verifier reran the producer helper independently and printed this exact ordered matrix before parsing it. The
copy fixture enables Git copy detection, changes the source to create a producer candidate, and uses a measured
75-percent-similar target; `.C C75` is therefore real Git output, not a forged acceptance row.

### Compound controls

Both real controls pass:

- staged modification followed by worktree rename emits type-1 `M.` plus type-2 `.R R100`; no `MR` row appears;
- staged rename followed by another worktree rename emits `R. R100` plus `.R R100`; no `RR` row appears.

The public parser accepts each complete multi-record output in order. This proves that rejecting mixed/two-sided XY
does not discard the corresponding real compound histories.

### Negative closure

All 24 explicit negatives fail with `content_invalid`:

- ten symmetric score-kind mismatches cover `R.`, `RM`, `RT`, `RD`, `C.`, `CM`, `CT`, `CD`, `.R`, and `.C`;
- fourteen mixed, two-sided, or unsupported pairs cover `MR`, `TR`, `AR`, `DR`, `CR`, `MC`, `TC`, `AC`, `DC`,
  `RC`, `RR`, `CC`, `RA`, and `CA`.

The prior score matrix also remains exact: unpadded `R0`, `R9`, `R99`, `R100`, and `C75` pass when kind-matched;
zero-padded, negative, missing-kind, wrong-kind, and greater-than-100 forms fail. Existing mode, submodule, object-ID,
path, framing, and failure-category regressions remain green.

## Prior foundation recheck

| Finding | Result | Fresh evidence |
|---|---|---|
| MUST: nested multi-component remote collision and unrelated near-suffix | PASS | `team/origin/fix/A20-1188` collides through classification and snapshot; `fix/A20-11880` and `prefix-fix/A20-1188` remain unrelated |
| MUST: real type-2 score bounds and XY coupling | PASS | ten producer families, `.C C75`, two compound controls, 24 negatives, and score boundaries all pass |
| MUST: canonical attempt category/disposition/phase/clock | PASS | forged permanent-category rows reject; exact active allowlist, attempt-zero, waiting/consumed, phase/revision, CAS, and clock tests pass |
| SHOULD: hooks-root before-open/during-scan identity | PASS | `lstat` to no-follow descriptor open/fstat/scan/final-lstat chain rejects both replacement races |
| SHOULD: all three optional readers stop at cap-plus-one | PASS | manifest, ownership, and attempt readers request exactly the 2,501st witness, no 2,502nd entry, and close the iterator |

### Manifest matrix

The canonical attempt validator still treats category, disposition, attempt, mutation phase, manifest revision, and
retry clock as one shape:

- only `attempt_backoff`, `scope_changed`, `lock_timeout`, `fetch_timeout`, and `fetch_command_failed` can be backoff;
- initial/scope-reset attempt zero requires phase `none`, null revision, and `retry_at == updated_at`;
- future transient retries remain waiting but cannot advance mutation phase;
- admission consumes a new revision/attempt and makes the retry clock equal the updated clock before prepared/added;
- permanent categories are manual-only, exhaustion requires attempt 3, and ready/removing retain exact revisions.

All permanent-category forged canonical reads, constructor combinations, waiting-versus-consumed behavior, exact CAS,
and elapsed/backward-clock cases pass. No new category/disposition/phase/clock contradiction was found.

## Verification evidence

| Gate | Exact result |
|---|---|
| Ten real-Git producer families + compound controls + 24 negatives | `36 passed, 82 deselected in 5.71s` |
| Independent printed producer matrix | exact ordered ten rows shown above; every public parse succeeded |
| Targeted prior-finding and manifest matrix | `105 passed, 104 deselected in 10.18s` |
| Focused Git-state + manifest suites | `209 passed in 28.37s` |
| Current nine-suite pre-provisioner superset | `445 passed in 74.70s` |
| Ruff `--no-cache` over Git-state, manifest, facade, and focused tests | `All checks passed!` |
| Product Pyright over Git-state, manifest, and facade | `0 errors, 0 warnings, 0 informations` |
| Executable AST-limit and lazy-facade tests | `2 passed in 0.18s` |
| Independent product AST scan | Git-state 114 functions, max 39 lines/nesting 4; manifest 101 functions, max 34 lines/nesting 3 |
| Fresh lazy-facade processes | cold `False/False`; Git access `True/False`; manifest access `False/True` |
| Tracked whitespace | `git diff --check` exit 0, no output |
| Seven owned untracked-file whitespace checks | expected no-index exit 1; zero whitespace output each; final byte `0a` |
| Installed producer | `git version 2.53.0` |

The nine-suite command covered routing contract, storage, decision, Git objects, runtime, route dispatch, worktree
contract, manifest, and Git-state. It is the required 445-test superset and excludes the intentionally paused
provisioner/runtime draft.

An independent delegated audit separately returned PASS with zero MUST/SHOULD, reran 209 focused and 445 superset
tests, and confirmed the prior nested-remote, manifest, hooks, optional-reader, and correction-scope invariants.

## Contradiction and quality review

The forced-reflection model, current implementation, producer fixtures, negative fixtures, and correction report now
agree. The iteration-3 `.R`/`.C` defect is closed. No missing requirement, unnecessary product addition,
interpretation gap, security issue, performance issue, or test-quality blocker was found. The descriptor and bounded-
scan defenses remain intact, and parser failures retain the existing fail-closed category.

Questions for the author: none. Positive result: the final helper expresses the authoritative type-2 allowlist in one
place and makes score coupling symmetric without weakening the common record parser.

## Scope and safety

Read: the forced-reflection report, iteration-3 FAIL, post-reflection correction report, current Git-state/manifest/
facade code, focused tests, and prior repair boundaries. All claims above were re-executed from current files.

This verifier wrote only this report through `apply_patch`. It made no product/test edit, network request, live
repository/AIDT operation, commit, branch/ref mutation, activation, or repair. Real Git checks used only installed Git
and disposable temporary repositories.
