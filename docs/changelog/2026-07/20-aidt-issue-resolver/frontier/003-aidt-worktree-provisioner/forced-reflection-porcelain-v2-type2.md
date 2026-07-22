# Forced reflection: porcelain-v2 type-2 grammar

Date: 2026-07-21

## Decision

Authorize exactly one bounded post-reflection correction loop before Frontier 003 lifecycle work resumes.

The failed iteration-3 recheck is a model defect, not another isolated edge case. The current parser assumes every
type-2 rename/copy marker is index-side. Git defines `X` as the index state and `Y` as the worktree state, and Git
2.53.0 produces valid type-2 records with worktree-side `.R` and `.C`. This reflection replaces the inferred
index-only grammar with the exact matrix below. If the one correction loop does not satisfy the stated stop
condition, mark the foundation blocked for manual parser review rather than opening another repair iteration.

No product or test change is authorized by this document itself.

## Authoritative model

Source: [Git `status` documentation](https://git-scm.com/docs/git-status). The current page identifies the manual as
last updated for Git 2.53.0, matching the installed `git version 2.53.0` used for the isolated fixtures.

The documentation establishes three facts that must be combined rather than inferred independently:

1. In tracked `XY`, `X` describes the index relative to HEAD and `Y` describes the worktree relative to the index;
   porcelain v2 uses `.` for the unchanged side.
2. A type-2 record is `2 <XY> ... <X><score> <path><sep><origPath>`, where the score token's first character is the
   rename/copy kind and the numeric portion is the similarity percentage.
3. The short-format table allows index-side `R|C` with worktree state unchanged, modified, type-changed, or deleted.
   It also recognizes rename/copy on the worktree side.

The `<X>` in the documentation's `<X><score>` token is not the index-side `X` position of `XY`; it is the independent
`R|C` score-kind byte. The parser must determine which `XY` side owns that byte and compare them.

## Frozen type-2 grammar

### Valid `XY`

The complete accepted set is:

```text
R.  RM  RT  RD
C.  CM  CT  CD
.R  .C
```

Equivalently:

```text
index-side:    XY = (R|C)(.|M|T|D)
worktree-side: XY = .(R|C)
```

Exactly one side carries the rename/copy kind:

```text
if X in {R, C} and Y in {., M, T, D}: kind = X
else if X == . and Y in {R, C}:       kind = Y
else:                                  invalid type-2 XY
```

The score token must be the same kind followed by an unpadded decimal percentage from 0 through 100:

```text
score-token = kind ( "0" | [1-9][0-9]? | "100" )
```

Examples: `R0`, `R9`, `R99`, `R100`, and `C75` are valid when their kind matches `XY`. `R00`, `R01`, `R099`,
`R101`, negative values, a missing kind, or any non-`R|C` kind are invalid.

### Score-kind coupling

| `XY` family | Required score kind | Mismatch examples that must fail |
| --- | --- | --- |
| `R.`, `RM`, `RT`, `RD` | `R` | `R. C75`, `RM C100` |
| `C.`, `CM`, `CT`, `CD` | `C` | `C. R75`, `CD R100` |
| `.R` | `R` | `.R C75` |
| `.C` | `C` | `.C R75` |

### Rejected mixed cases

Reject every other pair, including:

- index states followed by a worktree rename/copy: `MR`, `TR`, `AR`, `DR`, `CR`, `MC`, `TC`, `AC`, `DC`, `RC`;
- two-sided rename/copy pairs: `RR`, `CC`, `RC`, `CR`;
- index-side rename/copy followed by unsupported worktree states: `RA`, `CA`, `RR`, `CR`, `CC`;
- ordinary, conflict, untracked, or ignored markers presented as type 2.

This does not lose real compound changes. Git 2.53.0 represented an attempted staged-modification plus worktree
rename as a type-1 `M.` row plus a type-2 `.R R100` row, not one `MR` row. It represented a staged rename followed by
another worktree rename as two type-2 rows, `R. R100` and `.R R100`, not one `RR` row. Parser acceptance should follow
producer records, not synthesize combined states that the producer separates.

### Unchanged common fields

The correction must not alter the existing validation for:

- `<sub>` and its coupling to mode `160000`;
- the three allowed Git modes and their current HEAD/index/worktree positions;
- both lowercase SHA-1 object IDs;
- NUL framing, target/original path order, path canonicalization, duplicate detection, byte/path/count caps, or
  failure category.

Only type-2 `XY` recognition and score-kind extraction/coupling may change. In particular, real `RT`/`CT` rows vary
the worktree mode through the existing mode validator; accepting them does not justify loosening mode or submodule
rules.

## Producer evidence

All observations below came directly from `git status --porcelain=v2 -z --untracked-files=all` in disposable
temporary repositories. The shared worktree, refs, index, and commits were not mutated.

### Positive matrix

| Producer setup | Observed `XY` | Observed score | Current parser |
| --- | --- | --- | --- |
| staged exact rename | `R.` | `R100` | accepts |
| staged rename with one pre-stage content change | `R.` | `R99` | accepts |
| staged rename, then worktree modification/type-change/deletion | `RM` / `RT` / `RD` | `R100` | accepts |
| staged 75%-similar copy with a changed source | `C.` | `C75` | accepts |
| staged copy, then worktree modification/type-change/deletion | `CM` / `CT` / `CD` | `C75` | accepts |
| committed source moved, target marked intent-to-add | `.R` | `R100` | `content_invalid` |
| 75%-similar worktree copy, changed source, target intent-to-add | `.C` | `C75` | `content_invalid` |

For representative `R.`, `C.`, `.R`, and `.C` rows, Git emitted `N...`, modes `100644 100644 100644`, and valid
object IDs; the only acceptance difference was the side containing `R|C`. The `RT`/`CT` producers exercised a real
worktree type change through the existing mode grammar.

### Negative producer controls

| Attempted compound change | Git 2.53.0 output | Forbidden synthetic shortcut |
| --- | --- | --- |
| staged modification, then worktree rename | type 1 `M.` plus type 2 `.R R100` | `2 MR ... R100` |
| staged rename, then worktree rename | type 2 `R. R100` plus type 2 `.R R100` | `2 RR ... R100` |

These controls explain why accepting `.R` and `.C` does not require accepting mixed or two-sided `XY` values.

## Why the earlier inference failed

The iteration-2 repair started from negative synthetic examples (`MR`, `RR`) and an index-side real fixture
(`R99`). It correctly rejected the synthetic pairs, but inferred the stronger and false rule that `R|C` must always
occupy `XY[0]`. Its positive tests then generated only `R.` and `C.` rows, so every green result restated the same
assumption.

That inference collapsed two independent dimensions:

- which comparison contains the rename/copy: HEAD-to-index or index-to-worktree;
- which score kind describes the source/target similarity: `R` or `C`.

The short-format `XY` semantics and the type-2 score token were not recombined before the allowlist was frozen. Real
producer coverage was also asymmetric: it proved index-side rename but never produced worktree-side rename/copy.
Finally, `MR` and `RR` were treated as evidence against worktree-side changes even though Git represents those
compound histories as multiple records. The correct method is documentation-first semantic decomposition, followed
by at least one real producer for every accepted family and producer controls for rejected combinations.

## One-loop correction authorization

Recommended: yes, authorize one post-reflection correction loop with this exact scope:

1. Add red-first real Git fixtures for `.R R100` and `.C C75`, retaining producer fixtures for `R99` and `C75` on
   the index side. Freeze all ten valid `XY` values through producer or table-driven regressions.
2. Add symmetric score-kind mismatch tests for both sides and retain the score-boundary/zero-padding matrix.
3. Add the two producer controls proving compound changes become multiple records; keep explicit rejection of
   `MR`, `RR`, `RC`, `CR`, and the remaining non-matrix pairs.
4. Make the smallest parser change that derives one active `R|C` kind from `XY` and compares it to the score token.
   Do not modify common mode, submodule, object-ID, path, framing, or cap validation.

## Stop condition

The exception loop is complete only when all of the following are true:

- producer-generated `R.`, `RM`, `RT`, `RD`, `C.`, `CM`, `CT`, `CD`, `.R`, and `.C` rows parse with the matching
  unpadded score kind;
- producer controls emit separate records for staged-plus-worktree compound changes, while forged mixed/two-sided
  rows and all score-kind mismatches fail closed;
- the prior score bounds, modes, submodule coupling, object IDs, paths, NUL framing, caps, and failure category remain
  unchanged and green;
- the focused Git-state and manifest suites pass, and the current 410-test nine-suite pre-provisioner baseline passes
  as a 410-or-larger superset;
- Ruff, product Pyright, AST limits, lazy-facade checks, and tracked plus owned-file whitespace/EOF checks pass;
- one fresh independent recheck reproduces the real Git producer matrix and reports zero remaining MUST or SHOULD
  foundation findings.

Any failure after this bounded loop stops Frontier 003 at foundation review. It must not trigger a fifth implicit
Build/Verify cycle or allow lifecycle/provisioner work to resume.

## Scope and safety

This reflection read the R-LOOP, PLAN parser/status requirements, iteration-3 recheck, current parser/tests, the
official Git documentation, and installed Git 2.53.0 producer output. It writes only this report. It made no product,
test, facade, runtime, workflow, live repository, network state, branch/ref, shared commit, Jira, AIDT, or Jenkins
mutation.
