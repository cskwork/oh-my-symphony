# Frontier 003 Git-state repair iteration 2 report

Date: 2026-07-21

## Verdict

PASS.

The remaining nested-remote collision, hooks-root replacement, and porcelain rename/copy grammar findings are repaired
through the existing public Git-state interfaces. The finalized focused suite passes 83 tests, and the current
pre-provisioner compatibility superset passes 410 tests. No manifest, provisioner, facade, runtime, workspace, or core
file was changed.

No live repository, network, Jira, AIDT checkout, acceptance operation, commit, or branch/ref mutation outside isolated
temporary Git fixtures was used.

## Repairs

### MUST-1 - Nested remote names cannot hide an exact feature-ref suffix

`src/symphony/aidt_worktree/git_state.py:1279-1287` now recognizes every parsed
`refs/remotes/<nonempty-remote-name>/<exact-derived-branch>` by its exact prefix and suffix. It no longer assumes the
remote name has one component, so valid names such as `team/origin` collide. The nonempty prefix check prevents the
branch itself from being mistaken for a remote-qualified ref; the strict ref parser remains authoritative for the full
ref grammar.

Public regressions at `tests/test_aidt_worktree_git_state.py:906` and `:949` use real temporary Git refs. They prove:

- Git accepts the remote name `team/origin`;
- `refs/remotes/team/origin/fix/A20-1188` makes `classify_target_artifacts` return `AMBIGUOUS`;
- the repository snapshot path independently raises bounded `collision` for the same ref;
- `fix/A20-11880` and `prefix-fix/A20-1188` near-suffixes remain `ABSENT` and snapshot normally.

### MUST-3 - Porcelain type-2 XY and similarity score match Git's wire format

`src/symphony/aidt_worktree/git_state.py:1565-1576` accepts canonical `R|C` followed by the non-padded decimal range
0..100 and requires that prefix to match the index-side XY kind. `src/symphony/aidt_worktree/git_state.py:1603-1604`
accepts type-2 XY only when the index side is `R|C` and the worktree side is `.`, `M`, `T`, or `D`; impossible `MR`,
`RR`, and analogous shapes cannot pass merely because either byte contains `R|C`.

The regressions at `tests/test_aidt_worktree_git_state.py:294-397` include:

- a real temporary repository whose staged rename emits `R99`, then passes the public parser;
- canonical `R0`, `R9`, `R99`, `R100`, and `C75` acceptance;
- zero-padded, overlong, negative, missing-kind, wrong-kind, and >100 rejection;
- rename/copy score-prefix versus XY-kind mismatch rejection;
- rejected command-specific XY witnesses including `M.`, `MR`, and `RR`.

### SHOULD-1 - Hooks-root proof is descriptor-bound across enumeration

`src/symphony/aidt_worktree/git_state.py:618-683` now:

1. takes a pathname `lstat` witness;
2. opens the exact hooks directory with the shared `O_DIRECTORY|O_NOFOLLOW` flags;
3. requires descriptor `fstat` identity to equal the pathname witness;
4. enumerates and no-follow-stats entries through the descriptor;
5. rechecks pathname identity after enumeration;
6. closes the descriptor on success and every failure path.

The entry loop checks the 2,500 cap before materializing or statting the cap-plus-one witness, preserving the prior
bounded-scan guarantee. Regressions at `tests/test_aidt_worktree_git_state.py:1053-1160` replace the directory with a
symlink immediately before open and during descriptor enumeration, retain static symlink/executable-hook rejection,
and prove no overread beyond entry 2,501.

## TDD evidence

### Red - nested remote

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py::test_nested_remote_feature_ref_blocks_classification_and_snapshot
```

Result before repair: `1 failed`; the public classifier returned `ABSENT` instead of `AMBIGUOUS` for
`refs/remotes/team/origin/fix/A20-1188`.

### Red - real Git rename score and impossible XY

After staging the changed target, the real temporary repository emitted exact `R99` and the parser raised
`AidtWorktreeFailure("content_invalid")`:

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py::test_status_parser_accepts_real_git_non_padded_rename_score
```

Result before score repair: `1 failed` at `_rename_status`.

The expanded rejected-XY matrix then failed because `MR` did not raise. A separate prefix-coupling witness also failed
because XY `R.` with score `C75` was accepted. Each was made green with the smallest corresponding grammar change.

### Red - hooks replacement

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py::test_hooks_root_replacement_before_descriptor_open_is_rejected
```

Result before repair: `1 failed`; the old pathname scanner did not call descriptor open, the replacement did not
trigger, and `worktree add` proceeded instead of raising `protocol_invalid`. The during-scan test was added at the
shared descriptor solution boundary and proves the post-enumeration identity check.

## Green verification

### Focused Git-state suite

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_git_state.py
```

Result: `83 passed in 40.60s`.

### Current pre-provisioner compatibility superset

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_routing_contract.py \
  tests/test_aidt_routing_storage.py \
  tests/test_aidt_routing_decision.py \
  tests/test_aidt_routing_git_objects.py \
  tests/test_aidt_routing_runtime.py \
  tests/test_aidt_route_dispatch_contract.py \
  tests/test_aidt_worktree_contract.py \
  tests/test_aidt_worktree_manifest.py \
  tests/test_aidt_worktree_git_state.py
```

Result: `410 passed in 94.20s`. This is the current collected superset of the prior 349-test baseline after the settled
manifest iteration-2 repairs and the final score-kind coupling regression.

### Static and structure gates

```text
rtk ../../.venv/bin/ruff check --no-cache \
  src/symphony/aidt_worktree/git_state.py \
  tests/test_aidt_worktree_git_state.py
```

Result: `All checks passed!`.

```text
rtk ../../.venv/bin/pyright src/symphony/aidt_worktree/git_state.py
```

Result: `0 errors, 0 warnings, 0 informations` in the requested product scope.

The independent AST scan found 113 functions, maximum 39 lines (`fetch_production_base`), and maximum nesting 4
(`_scan_ignored_directory`). The executable AST and lazy-facade gates passed together: `2 passed in 0.20s`.

### Whitespace

`rtk git diff --check` returned exit 0 with no output. The owned product and test files remain untracked in the shared
worktree, so `rtk git diff --no-index --check /dev/null <file>` returned the expected content-difference exit 1 and
empty whitespace output for both.

## Changed scope

- `src/symphony/aidt_worktree/git_state.py`
- `tests/test_aidt_worktree_git_state.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/git-state-repair-iteration2-report.md`

No other file was edited in this task.
