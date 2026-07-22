# Post-reflection porcelain-v2 type-2 correction

## Decision

The type-2 parser now accepts exactly the producer-backed XY matrix from the forced reflection. It derives the sole
active rename/copy kind from either side of XY and requires the score marker to match that kind. No other parser,
mode, submodule, path, cap, mutation, or facade behavior changed.

## Corrected model

For index-side rows, `X` is `R` or `C` and `Y` is one of `.MTD`. For worktree-side rows, `X` is `.` and `Y` is `R`
or `C`. Every other shape is invalid. The score remains an unpadded decimal from 0 through 100 prefixed by the
derived kind.

| Producer family | Exact rows exercised | Exact score |
|---|---|---|
| Index rename | `R.`, `RM`, `RT`, `RD` | `R100` |
| Index copy | `C.`, `CM`, `CT`, `CD` | `C75` |
| Worktree rename/copy | `.R`, `.C` | `R100`, `C75` |

Each parameter row creates a fresh temporary Git repository, asserts the exact producer bytes `(XY, score,
destination, source)`, then asserts the public parser result. Copy fixtures enable Git copy detection and use a
producer-measured 75-percent-similar target.

Two real-Git controls prove compound changes remain separate records:

- staged modification followed by worktree rename emits type-1 `M.` plus type-2 `.R R100`, never `MR`;
- staged rename followed by another worktree rename emits `R. R100` plus `.R R100`, never `RR`.

Forged regressions reject the opposite score marker for all ten accepted families. They also reject `MR`, `TR`,
`AR`, `DR`, `CR`, `MC`, `TC`, `AC`, `DC`, `RC`, `RR`, `CC`, `RA`, and `CA`.

## TDD evidence

The first producer fixture emitted exact `.R R100` and failed in `_rename_status` with `content_invalid`. After the
minimal kind-derivation change, the same test passed. The fixture was then expanded through the remaining producer
families and controls.

| Gate | Exact result |
|---|---|
| Red, real worktree-side rename | `1 failed in 0.43s`; producer bytes were `.R R100` |
| Green, same fixture | `1 passed in 0.66s` |
| Ten-family real-Git producer matrix | `10 passed in 4.93s` |
| Two real-Git compound controls | `2 passed, 93 deselected in 1.48s` |
| Symmetric marker and mixed/two-sided rejection matrix | `24 passed, 94 deselected in 0.15s` |

## Verification

| Gate | Exact result |
|---|---|
| Focused Git-state suite | `118 passed in 45.63s` |
| Focused Git-state + manifest suites | `209 passed in 45.40s` |
| Current nine-suite pre-provisioner superset | `445 passed in 98.60s` |
| Ruff `--no-cache` over Git-state, manifest, facade, and focused tests | `All checks passed!` |
| Product Pyright over Git-state, manifest, and facade | `0 errors, 0 warnings, 0 informations` |
| Executable AST-limit and lazy-facade gates | `2 passed in 0.29s` |
| Fresh lazy-facade processes | cold `False/False`; Git access `True/False`; manifest access `False/True` |
| Tracked whitespace | `git diff --check` exit 0, no output |
| Three owned untracked-file whitespace/EOF checks | expected no-index exit 1 with no whitespace output; each final byte is `\n` |

The nine-suite command covered routing contract, storage, decision, Git objects, runtime, route dispatch, worktree
contract, manifest, and Git-state. It remains a 410-or-larger superset and excludes the intentionally paused
provisioner/runtime draft.

## Scope

Edited only:

- `src/symphony/aidt_worktree/git_state.py`;
- `tests/test_aidt_worktree_git_state.py`;
- this report.

All edits used `apply_patch`. The correction used only installed Git and disposable temporary repositories. It made
no network request, live Jira/AIDT or service-repository operation, commit, branch/ref activation, checkout, or
destructive change.
