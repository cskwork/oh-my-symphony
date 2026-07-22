# Frontier 003 Workspace Delegate RED Report

Date: 2026-07-22
Scope: test-only `WorkspaceManager` delegate boundary
Verdict: **INTENTIONAL RED / PRODUCT DELEGATE SEAM REQUIRED**

## Decision and theory

The workspace suite keeps the five public behaviors named by the core integration brief and now reaches each real
`WorkspaceManager` method independently despite the missing constructor seam. The UNMANAGED and HANDLED public
tests each parameterize `path_for`, `create_or_reuse`, and `before_run`, so a missing earlier keyword cannot mask a
later method contract. A test-only fallback attaches the fake runtime when the frozen `aidt_runtime=` constructor
keyword is rejected; every case retains a final assertion requiring native constructor support. This bridge changes
no product source and prevents one missing keyword from masking the deeper behavioral RED.

The tests now prove these contracts:

- real UNMANAGED `path_for`, `create_or_reuse`, and `before_run` independently prove generic path, create/marker,
  and hook behavior only after exact `UNMANAGED`;
- real HANDLED `path_for`, `create_or_reuse`, and `before_run` independently prove the delegated path, exact
  prepared guard, and guard delegation without generic side effects;
- generation/admission and generation/guard are exact pairs: either half alone is rejected before delegate, generic
  workspace, owner-marker, hook, Git, network, Jira, or backend activity;
- keyworded `remove` remains a delegated, non-generic operation for `UNMANAGED`, `HANDLED`, `OWNED_PRESERVED`, and
  `OWNED_ERROR`; only a later explicit positional remove may execute legacy cleanup after `UNMANAGED`;
- legacy unmanaged create, hook, owner marker, return fields, sanitization, and positional lifecycle remain intact.

Every public case installs fail-fast sentinels on workspace subprocess/Git execution, socket connection creation,
Jira intake, and backend construction, then asserts zero calls. Tests use only temporary paths, frozen public DTOs,
one ordered event list, and a fake process runtime. They use no live repository, network, Jira, backend, completion
authority, or product mutation.

## Installed RED coverage

- `test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return`
- `test_delegate_handled_create_returns_guard_without_generic_side_effects`
- `test_delegate_owned_create_and_before_run_never_fall_back`
- `test_keyworded_remove_is_a_non_destructive_unmanaged_probe`
- `test_keyworded_owned_remove_preserves_before_generic_hook_or_rmtree`

Parameterization now covers independent UNMANAGED and HANDLED path/create/before-run cases, two owned `path_for`
outcomes, four owned create/before-run outcomes, all four half-paired create/before-run argument shapes, and
`HANDLED` plus both owned keyworded-remove outcomes. The 20 collected cases retain exactly the five frozen public
function names and preserve the original 16-case behavior matrix.

## Exact RED evidence

Collection:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_workspace.py -k 'delegate or keyworded'
20/61 tests collected (41 deselected) in 0.22s
```

Focused delegate slice:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=line \
  tests/test_workspace.py -k 'delegate or keyworded'
20 failed, 41 deselected in 0.36s
```

The intentional failures are distributed across the missing public behavior instead of being masked by either the
constructor or an earlier lifecycle method:

- the six independent UNMANAGED/HANDLED lifecycle cases fail at their own boundary: two `path_for`, two
  `create_or_reuse`, and two `before_run` cases reject `aidt_generation`;
- owned path/create/before-run and all four half-pair cases receive raw `TypeError` where the tests require bounded
  `AidtWorkspaceOperationError(category, ref)` and zero fallback events;
- unmanaged, handled, and owned keyworded-remove cases fail because `WorkspaceManager.remove` rejects `identifier`.

No case accepted the generic path on an owned or malformed specialized request. The test-only constructor bridge's
final assertion remains armed so method repair without the frozen public constructor still stays RED.

Owned-error assertions now exclude the managed workspace, the distinct generic manager root, and their enclosing
temporary root. The former hostile-text assertion was removed because no valid sealed `DelegateResult` carries
nested exception text; the suite makes no claim about protocol-violating fake exceptions.

## Compatibility and static evidence

Unchanged positional constructor/create/before-run controls:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_workspace.py::test_create_and_reuse \
  tests/test_workspace.py::test_sanitization \
  tests/test_workspace.py::test_before_run_aborts_attempt
3 passed in 0.68s
```

Static gates:

```text
rtk run "../../.venv/bin/ruff check --no-cache tests/test_workspace.py"
All checks passed!

rtk run "../../.venv/bin/pyright tests/test_workspace.py"
0 errors, 0 warnings, 0 informations

AST scan of the 13 delegate helpers and public tests
functions 13
max_lines 49
max_nesting 2
over_50 []
over_nesting_4 []
missing []
```

Repository and no-index whitespace checks for the test and both audit documents returned no findings. The no-index
commands returned the expected content-difference exit `1` with no output; these checks do not mutate the
repository.

## Scope

Changed paths are limited to:

- `tests/test_workspace.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/workspace-delegate-red-report.md`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/workspace-red-adversarial-review.md`

No product source, live AIDT checkout, repository state, network, Jira, backend, commit, merge, push, deployment, or
completion authorization was touched.

## Final relative-path false-green closure — 2026-07-22

The owned-error fixture now gives the generic manager root, managed-worktree parent, and enclosing owned-test root
three distinct hostile path-component sentinels. Its bounded-error helper retains the required exact exception type,
allowlisted `category`, and canonical identifier `ref`, then rejects both the four complete absolute fixture paths
and the three actual relative path components. It does not freeze an exact message and does not introduce a raw,
protocol-violating delegate exception.

The original 20-case distribution is unchanged: five frozen public names, six independent UNMANAGED/HANDLED
lifecycle cases, six valid owned dispositions, four malformed half-pairs, and four keyworded-remove dispositions.
The constructor bridge, generic-effect sentinels, remove matrix, and positional legacy controls remain intact.

Final evidence:

```text
collection: 20/61 tests collected (41 deselected) in 0.32s
focused native RED: 20 failed, 41 deselected in 0.58s
relative manager-root leak mutant: 6 failed, 14 passed, 41 deselected in 0.21s
legacy positional controls: 3 passed in 0.50s
Ruff: All checks passed!
Pyright: 0 errors, 0 warnings, 0 informations
AST: functions 14, max_lines 49, max_nesting 2, no size/nesting/missing violations
```

The leak mutant was a temporary pytest plugin that supplied the complete frozen delegate seam but appended only
`manager.root.name` to valid owned-error messages. Exactly the six valid owned-disposition cases rejected that
relative component; the other 14 cases passed. The plugin was removed immediately after the test run and no product
source was edited. The repository whitespace check exited 0 with no findings; separate no-index checks for the test
and both reports returned the expected content-difference status 1 with no whitespace output.
