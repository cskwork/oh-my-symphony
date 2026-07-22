# Frontier 003 foundation repair recheck report

Date: 2026-07-21

## Verdict

FAIL.

The repaired foundation passes the 113-test focused suite, the 349-test pre-provisioner superset, Ruff, scoped
Pyright, AST limits, lazy-facade checks, and whitespace checks. The prior locked-registration, origin-control,
ignored-directory descriptor, elapsed/backward-clock, exact revision, and helper-coverage findings are repaired.
Three MUST and two SHOULD gaps remain: nested remote names evade feature-ref collision detection, type-2 status grammar
both accepts impossible XY pairs and rejects real Git scores, non-retryable attempt categories can still persist as
backoff and promote to ready, hooks-root replacement is still followed by pathname, and optional collision scans
materialize the entire directory before enforcing the cap.

No product or test file was changed by this verifier.

## Findings

### MUST-1 - Valid nested-remote feature refs are still classified as absent

Path: `src/symphony/aidt_worktree/git_state.py:1245`

`_has_remote_target_ref` requires the suffix below `refs/remotes/` to contain exactly one more slash than the feature
branch. That recognizes `origin/fix/A20-1188` and `backup/fix/A20-1188`, but excludes a valid remote name containing a
slash. Git 2.53.0 accepted both the remote name `team/origin` and the full ref
`refs/remotes/team/origin/fix/A20-1188`. A fresh public-classifier probe returned `AMBIGUOUS` for the origin and backup
refs but `ABSENT` for that nested-remote ref with the same exact feature-branch suffix.

The contract rejects any existing remote-tracking feature ref before create/recovery; it does not restrict remote
names to one component. `git worktree add -b` could therefore create the local feature branch despite a conflicting
valid remote-tracking ref. This leaves the prior Git MUST-1 only partially repaired.

Required correction: treat every valid `refs/remotes/<nonempty-remote-name>/<exact-derived-branch>` as a collision,
without imposing a one-component remote-name grammar. Add nested valid remote names to both target classification and
snapshot/recovery fixtures.

### MUST-2 - Type-2 status grammar accepts impossible XY and rejects real Git scores

Paths: `src/symphony/aidt_worktree/git_state.py:1531`,
`src/symphony/aidt_worktree/git_state.py:1569`

`_valid_rename_xy` accepts any pair containing `R` or `C` within broad per-position character sets. That admits
impossible type-2 pairs such as `MR` and `RR`; both fresh synthetic rows parsed as renamed entries. Git's installed
status matrix permits index rename/copy with worktree `.MTD`, or unchanged index with worktree `R/C`, not those pairs.

The score regex has the inverse problem: it requires `R|C` plus exactly three digits (`100` or `000-099`). Git's
porcelain grammar uses an unpadded percentage and documents values such as `C75`. An isolated Git 2.53.0 repository
with a staged rename emitted an exact `R99` type-2 record; `parse_status_porcelain_v2` rejected that real output as
`content_invalid`. A synthetic `C75` row was likewise rejected. This leaves the prior Git MUST-3 open despite the
current regression test expecting `R99` to be invalid.

Required correction: encode the exact type-2 XY matrix and the real `R|C` score grammar from 0 through 100 without
invented zero-padding. Differential tests must include real non-100 rename/copy output as well as impossible `MR`,
`RR`, and related pairs.

### MUST-3 - Non-retryable failure categories can persist as backoff and promote to ready

Paths: `src/symphony/aidt_worktree/manifest.py:1033`,
`src/symphony/aidt_worktree/manifest.py:1435`

`_valid_attempt` validates category and disposition independently. `_active_provision_attempt` then accepts every
backoff category except literal `ready`; it does not restrict active backoff to initial/scope-reset or the three
retryable categories. Fresh probes replaced a consumed attempt's category with `collision`, `attempt_exhausted`, and
`protocol_invalid`. All three records passed construction and `none -> prepared -> ready`. A second probe persisted
`category=collision, disposition=backoff, attempt=1` through exact `persist_attempt` CAS and read it back unchanged.

Collision, protocol, exhaustion, and the other permanent categories are manual-only under Amendment I. The repaired
manual-disposition tests do not cover an impossible category/disposition pair, so the prior manifest MUST-3 remains
open at both durable-schema and helper-source boundaries.

Required correction: couple category, disposition, attempt, phase, and retry shape in `_valid_attempt`, and define
the active-provision source as a positive allowlist of the exact initial/scope-reset/retryable categories. Add
canonical read/CAS and constructor regressions for non-retryable-category backoff records.

### SHOULD-1 - Hooks-root replacement is followed after the no-follow check

Path: `src/symphony/aidt_worktree/git_state.py:618`

The repair replaces `exists()` with `lstat()`, so a pre-existing hooks-root symlink is rejected. It still calls
`os.scandir(hooks)` by pathname after that check. A direct fault probe renamed the checked directory and installed a
symlink to an empty replacement immediately when `scandir` was called; `_reject_executable_hooks` returned normally
and the path was a symlink afterward. Thus the hooks-directory object can still be followed across the check/use
race. The fixed `core.hooksPath=/dev/null` continues to limit this to SHOULD, as in the original finding.

Required correction: open the hooks root with `O_DIRECTORY|O_NOFOLLOW`, bind pathname `lstat` to descriptor `fstat`,
enumerate through that descriptor, and recheck identity after enumeration. Add replacement-before-open and
replacement-during-scan fixtures.

### SHOULD-2 - Optional collision scans enforce their cap after unbounded materialization

Path: `src/symphony/aidt_worktree/manifest.py:1125`

`_read_optional_record` calls `list(os.scandir(path.parent))` and only then compares the length with
`MAX_REGISTRY_ENTRIES`. A sentinel iterator that supplied the 2,501st cap-plus-one witness raised because the helper
requested another entry instead of stopping. The three optional readers therefore perform unbounded allocation
before the advertised 2,500-entry registry limit, despite the repair report's “capped parent-directory scan” claim.

Required correction: iterate with cap-plus-one accounting and stop immediately on entry 2,501, retaining only the
bounded names needed for exact NFC/case-fold collision detection. Add the same no-overread sentinel for all three
public optional readers.

### NIT

None.

## Prior-finding recheck matrix

| Original finding | Result | Fresh evidence |
|---|---|---|
| Git MUST-1 remote-tracking feature collision | FAIL | One-component remotes are ambiguous; valid nested remote is absent (MUST-1) |
| Git MUST-2 locked create/remove target | PASS | Locked S2 and cleanup-pre regression passed |
| Git MUST-3 command-specific parser grammar | FAIL | Type-1/unmerged/ref cases pass, but impossible type-2 XY is accepted and real `R99` is rejected (MUST-2) |
| Git MUST-4 raw/decoded origin controls | PASS | Raw/encoded path and SSH-username controls rejected |
| Git MUST-5 ignored-directory no-follow proof | PASS | Before-open, during-enumeration, and before-second-status replacement regressions passed |
| Git SHOULD-1 cap-plus-one enumeration | PASS | Ignored-content and hooks sentinel regressions passed |
| Git SHOULD-2 hooks-root symlink | FAIL | Static symlink rejected, but replacement-to-symlink race accepted (SHOULD-1) |
| Manifest MUST-1 optional collision behavior | PASS | Exact ENOENT/read, three case aliases, and invalid record shapes passed |
| Manifest MUST-2 elapsed/backward clock behavior | PASS | 0/1/600-second admission and phase cases passed; backward writes rejected |
| Manifest MUST-3 exact attempt transitions | FAIL | Manual/attempt-zero/revision tests pass, but permanent categories promote to ready (MUST-3) |
| Manifest SHOULD-1 helper coverage | PASS | All six helpers are now imported and behaviorally exercised |

## Verified invariants

- Remote-tracking collisions under ordinary one-component remotes, locked target registrations, raw/decoded origin
  controls, tested impossible type-1/type-u rows, and representative invalid refs fail closed. MUST-2 prevents the
  broader type-2 grammar claim.
- Git 2.53.0 and the product both accept `refs/heads/@`; Git and the product both reject
  `refs/heads/.hidden`. The prior report's `@` example is correctly superseded without weakening the real grammar.
- Ignored directories use `O_DIRECTORY|O_NOFOLLOW`, descriptor enumeration, before/open/after identity, retained
  witnesses, and a post-second-status recheck. Both ignored and hooks entry loops stop at cap-plus-one.
- Optional readers reject case aliases and existing symlink/wrong-mode/directory/malformed records; exact
  collision-free ENOENT alone returns `None`. SHOULD-2 concerns bounded enumeration, not those result semantics.
- Initial, admitted, prepared, added, ready, and removing helpers preserve whole-second UTC time and fixed manifest
  revisions for valid records. Forward elapsed time through 600 seconds remains valid; backward durable writes fail;
  `persist_attempt` retains exact revision CAS and durability behavior.
- The public facade exposes all six manifest helpers lazily. Cold import loads neither heavy module; Git-state access
  loads only `git_state`, and manifest-helper access loads only `manifest`.
- Git-state has 111 functions, maximum 39 lines and nesting 4. Manifest has 93 functions, maximum 34 lines and
  nesting 3. No function exceeds 50 lines or nesting 4.

## Verification evidence

| Gate | Exact result |
|---|---|
| Focused Git-state + manifest suites | 113 passed in 26.07s |
| Explicit prior-finding regression matrix | 50 passed in 10.73s |
| Current pre-provisioner superset | 349 passed in 81.03s |
| Ruff `--no-cache` over product/facade/focused tests | `All checks passed!` |
| Pyright over Git-state, manifest, and facade | 0 errors, 0 warnings, 0 informations |
| Independent AST check | Git-state max 39 lines/nesting 4; manifest max 34 lines/nesting 3 |
| Fresh lazy-facade processes | cold `False/False`; Git access `True/False`; manifest access `False/True` |
| Tracked whitespace | `git diff --check` exit 0, no output |
| Five owned untracked file checks | expected no-index exit 1; zero whitespace output each |
| Fresh semantic probes | reproduced nested-remote absence, impossible `MR`/`RR` acceptance, real Git `R99` rejection, permanent-category ready promotion/CAS persistence, hooks-root race, and optional-scan overread |

The repository-wide default Pyright invocation also ran. It reported 35 errors and 3 warnings in the explicitly
paused provisioner draft and unavailable optional application dependencies; the configured foundation-file scope
above is clean, and none of those diagnostics is in the repaired Git-state, manifest, or facade files.

## Scope and audit notes

Read: repository `AGENTS.md`; both original verifier reports; `git-state-repair-report.md`;
`manifest-helper-repair-report.md`; current Git-state, manifest, facade, and both focused test files; and the binding
PLAN clauses needed to adjudicate remote refs, origin grammar, caps, clocks, and attempt dispositions. Every prior
finding was rerun through the 50-test targeted matrix, then supplemented with fresh probes for uncovered edge shapes.

This verifier wrote only this report through `apply_patch`. It made no product/test edit, network request, live
repository/AIDT operation, commit, branch/ref mutation, or activation. Git grammar and nested remote-name checks used
only installed Git 2.53.0 and an isolated temporary repository.
