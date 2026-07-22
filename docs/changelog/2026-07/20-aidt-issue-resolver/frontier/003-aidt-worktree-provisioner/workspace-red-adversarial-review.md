# Frontier 003 WORKSPACE RED Adversarial Review

Verdict: **REQUEST CHANGES**.

The five frozen public test names collect as the claimed 16 cases and cover every required disposition on paper, but
the current RED does not independently execute all of the UNMANAGED/HANDLED method contracts, and its bounded-error
assertion permits a real manager-root path leak. Product implementation should remain blocked until those two proof
gaps are repaired without changing the frozen public names or weakening the 16-case contract.

## Blocking findings

### 1. Earlier `path_for` failures mask three downstream contracts

`test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return` calls `path_for` at
`tests/test_workspace.py:888` before the UNMANAGED `create_or_reuse` call at line 891.
`test_delegate_handled_create_returns_guard_without_generic_side_effects` likewise calls `path_for` at line 941
before HANDLED `create_or_reuse` at line 944 and HANDLED `before_run` at line 949.

The focused RED confirms the masking: those two cases stop with
`WorkspaceManager.path_for() got an unexpected keyword argument 'aidt_generation'`. They do not reach the claimed
UNMANAGED create path, HANDLED create path, HANDLED before-run barrier, or the latter's generic-hook suppression.
Owned create/before-run and all remove variants are independently reached by their parameterized cases, so this gap
is limited and repairable.

Required repair: invoke the operations before making the first assertion, recording each outcome independently, or
otherwise isolate their calls while retaining the five public names and 16 collected cases. For example, the
HANDLED case can capture `path_for`, `create_or_reuse`, and `before_run` outcomes separately and assert all three only
after every call has been attempted. A missing `path_for` keyword must not prevent RED evidence for the two later
methods.

### 2. The bounded-error test does not exclude the manager/root path

The owned-result cases correctly require `AidtWorkspaceOperationError`, the exact allowlisted category, and the
canonical identifier ref at `tests/test_workspace.py:1017-1026`. However, line 1037 excludes only `str(managed)`,
whose fixture path is `<tmp>/managed/A20-1203--lms-api`. The manager owns the distinct
`<tmp>/generic-workspaces` root. An implementation can include `self._root` in the exception message, violate the
frozen no-path rule, and still pass the current assertion.

Required repair: assert that `str(tmp_path)` (and therefore every fixture path derived from it) is absent from the
error text. The line 1038 `hostile-nested-detail` assertion is not evidence: no fixture seeds that text, and the
public runtime protocol is total—valid owned `DelegateResult` values contain only an allowlisted category and no
nested exception/value. Remove that vacuous assertion or document it only as a non-claim; do not invent an
out-of-protocol throwing delegate to repair this gap.

## Accepted coverage

- Constructor bridge is fail-closed: `_delegate_manager` marks fallback construction at lines 148-166 and every
  public case calls `_assert_native_delegate_constructor` at the end. Method repair without native
  `aidt_runtime=` support therefore remains RED; once construction succeeds, event assertions prove the supplied
  runtime is actually used.
- The matrix collects exactly five public names / 16 cases: `path_for` UNMANAGED/HANDLED/both owned states;
  create UNMANAGED/HANDLED/both owned states/two half-pairs; before-run HANDLED/both owned states/two half-pairs;
  and keyworded remove UNMANAGED/HANDLED/both owned states.
- Only an exact keyworded-remove `UNMANAGED` result permits a later explicit positional legacy remove. HANDLED and
  both owned remove dispositions preserve the sentinel before generic hooks or recursive removal.
- Every public case installs deny sentinels for workspace subprocess/Git execution, socket connection creation,
  Jira intake, and backend construction, and checks the call list remains empty. The intended UNMANAGED generic hook
  is a local test spy, not an external operation.
- The three legacy controls pass unchanged, and the test-only changes pass Ruff, Pyright, AST size/nesting, and
  whitespace checks.

## Independent execution evidence

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_workspace.py -k 'delegate or keyworded'
16/57 tests collected (41 deselected) in 0.25s

rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=line \
  tests/test_workspace.py -k 'delegate or keyworded'
16 failed, 41 deselected in 0.41s
```

Failure distribution: the two lifecycle cases fail first at `path_for`; six owned-disposition operation cases fail
because a raw `TypeError` is not the required bounded error; the four half-pair cases fail for the same reason; and
all four keyworded-remove cases fail at the missing `identifier` keyword. This is intentional product RED, but the
first two failures establish finding 1.

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_workspace.py::test_create_and_reuse \
  tests/test_workspace.py::test_sanitization \
  tests/test_workspace.py::test_before_run_aborts_attempt
3 passed in 0.33s

rtk ../../.venv/bin/ruff check --no-cache tests/test_workspace.py
All checks passed!

rtk ../../.venv/bin/pyright tests/test_workspace.py
0 errors, 0 warnings, 0 informations

AST scan of the 11 new helpers/public tests
functions 11
max_lines 50
max_nesting 2
over_50 []
over_nesting_4 []
missing []

rtk git diff --check -- tests/test_workspace.py
exit 0 with no output

rtk git diff --no-index --check /dev/null tests/test_workspace.py
exit 1 with no output (expected content-difference status)

rtk git diff --no-index --check /dev/null \
  docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/workspace-red-adversarial-review.md
exit 1 with no output (expected content-difference status)
```

No product or test file was edited by this review. No live AIDT checkout, Git mutation, network, Jira, backend,
completion authority, commit, merge, push, or deployment was used.

## Resolution — 2026-07-22

Status: **BOTH BLOCKING FINDINGS CLOSED IN TEST-ONLY RED**.

- Finding 1: the unchanged UNMANAGED and HANDLED public test names now each collect separate `path_for`,
  `create_or_reuse`, and `before_run` cases. All six calls execute independently; focused RED fails twice at each
  missing product method keyword instead of stopping both lifecycles at `path_for`.
- Finding 2: bounded owned errors now exclude `managed`, the distinct `manager.root`, and their enclosing
  `tmp_path`. The vacuous hostile-text assertion was removed; no out-of-protocol throwing fake was introduced.
- Preserved: constructor fail-closed assertion, all half-pairs, the full keyworded-remove disposition matrix,
  external-operation sentinels, the three legacy controls, and the five frozen public function names.

Resolution evidence: `20/61` cases collected under the five names; focused delegate RED `20 failed, 41 deselected`;
legacy controls `3 passed`; Ruff passed; Pyright reported zero errors/warnings; AST scan reported 13 functions,
49 maximum lines, nesting 2, and no violations. The original review verdict and evidence above remain unchanged as
the historical pre-repair record.

## Fresh final re-audit — 2026-07-22

Verdict: **REQUEST CHANGES**.

The earlier operation-masking blocker is closed: the two UNMANAGED/HANDLED public tests now collect six independent
`path_for`, `create_or_reuse`, and `before_run` cases, and fresh RED reaches the missing keyword on each method twice.
The earlier absolute-path blocker is also closed: the owned-error loop excludes the resolved managed path, the
distinct resolved manager root, and the enclosing resolved `tmp_path`; an injected absolute manager-root leak fails
all six owned-disposition cases.

One bounded-message false-green still violates the frozen contract. `tests/test_workspace.py:1087-1089` rejects only
the complete absolute strings for `managed`, `manager.root`, and `tmp_path`. An otherwise conforming test-only seam
whose `AidtWorkspaceOperationError` message is `<category>: generic-workspaces` passes all 20 focused cases. That
suffix is the manager root expressed as a relative path, while the frozen brief requires the message to contain no
path text. Chasing individual absolute and relative spellings is not a closed assertion.

Required repair: give the managed parent, manager root, and enclosing test root distinct hostile path-component
sentinels, then assert every unique component is absent from the error text in addition to the existing full-path
checks. This closes absolute and relative spellings without freezing an unrequired exact exception string. Keep the
existing exact exception type, `category`, and `ref` assertions. Do not add an out-of-protocol throwing runtime fake;
sealed `DelegateResult` values already provide all valid owned outcomes.

### Re-attack results

- Constructor bridge: a conforming in-memory seam passes `20/20`; a native constructor that ignores the supplied
  runtime fails 16 cases. The four half-pair cases still fail closed before runtime use, so their survival in that
  mutant is expected and does not mask constructor usage on a complete specialized request.
- Half-pairs: a mutant that delegates generation-only/admission-only and generation-only/guard-only requests fails
  exactly those four cases (`4 failed, 16 passed`).
- Dispositions and fallback: HANDLED-to-generic fallback fails the three HANDLED lifecycle cases; owned-to-generic
  fallback fails all six owned lifecycle cases; keyworded-UNMANAGED immediate removal fails its single probe before
  the explicit positional legacy remove.
- Removal: `UNMANAGED`, `HANDLED`, `OWNED_PRESERVED`, and `OWNED_ERROR` remain independently represented. Only the
  explicit positional call after exact keyworded `UNMANAGED` reaches the generic remove hook and recursive delete.
- Sentinels: injecting `subprocess.run` before lifecycle delegation trips the installed Git sentinel in all 12
  complete path/create/before-run cases. Jira, backend, and socket sentinels are installed by the same helper; the
  local hook spy remains the sole intended generic lifecycle effect.
- Leakage: an absolute-root mutant is rejected by all six owned cases (`6 failed, 14 passed`), but the relative-root
  mutant passes (`20 passed, 41 deselected`). This passing mutant is the blocking proof above.
- Masking: the focused native RED reports two `path_for` keyword failures, two `create_or_reuse` keyword failures,
  and two `before_run` keyword failures before the owned, half-pair, and remove failures. No earlier lifecycle call
  prevents a later method contract from executing.

The mutation probes used an ephemeral `/tmp` pytest plugin and changed no repository product or test file.

### Fresh command evidence

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_workspace.py -k 'delegate or keyworded'
20/61 tests collected (41 deselected) in 0.21s

rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=line \
  tests/test_workspace.py -k 'delegate or keyworded'
20 failed, 41 deselected in 0.28s

rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_workspace.py::test_create_and_reuse \
  tests/test_workspace.py::test_sanitization \
  tests/test_workspace.py::test_before_run_aborts_attempt
3 passed in 0.33s

rtk run "../../.venv/bin/ruff check --no-cache tests/test_workspace.py"
All checks passed!

rtk run "../../.venv/bin/pyright tests/test_workspace.py"
0 errors, 0 warnings, 0 informations

AST scan of the 13 delegate helpers/public tests
functions 13
max_lines 49
max_nesting 2
over_50 []
over_nesting_4 []
missing []
```

Repository and no-index whitespace checks for `tests/test_workspace.py`, the delegate RED report, and this review
returned no findings. The no-index commands returned the expected content-difference exit `1` with no output. No
product/test edit, live AIDT checkout, Git mutation, network, Jira, backend, completion authority, commit, merge,
push, or deployment was used.

## Final re-audit resolution — 2026-07-22

Verdict: **ACCEPTED — RELATIVE-PATH FALSE-GREEN CLOSED**.

The final blocker is closed test-only. The generic manager root, managed-worktree parent, and enclosing owned-test
root now have distinct hostile path-component sentinels. The shared owned-error assertion rejects all three actual
components as well as the complete resolved managed path, manager root, owned-test root, and `tmp_path`. It still
requires the exact `AidtWorkspaceOperationError` type, allowlisted `category`, and canonical identifier `ref`
without demanding an unrequired exact exception string.

An evaluator-style temporary seam that otherwise satisfies all workspace delegate contracts but emits the relative
manager-root component fails exactly the six valid owned-disposition cases (`6 failed, 14 passed, 41 deselected`).
The temporary plugin was removed after execution. No out-of-protocol throwing delegate was added.

Fresh native evidence remains the intended independently distributed product RED: `20/61` collected under the same
five public names and `20 failed, 41 deselected`. The three positional legacy controls pass. Ruff passes, Pyright
reports zero errors and warnings, and the 14-function delegate AST scan reports a 49-line maximum, nesting 2, and no
violations. Constructor bridging, all four half-pairs, the complete remove matrix, and external-operation sentinels
remain present. Final repository and no-index whitespace checks produced no findings. The earlier findings, attack
results, and verdicts above are retained as historical evidence.

## Final independent audit after relative-path repair — 2026-07-22

Verdict: **APPROVE**.

The repaired WORKSPACE RED is sufficient to unblock the frozen product seam. Fresh collection is exactly 20 of 61
cases under the same five public names. The intentional native run fails all 20 and distributes at the intended
boundary: two path_for keyword failures, two create_or_reuse keyword failures, two before_run keyword failures,
six valid owned-disposition bounded-error failures, four malformed half-pair bounded-error failures, and four
keyworded-remove failures. No earlier lifecycle failure masks a later method contract.

The constructor bridge remains fail-closed: fallback construction records its use and every public case requires
native aidt_runtime constructor support after its behavioral assertions. UNMANAGED and HANDLED independently cover
path, create, and before-run. The owned matrix covers both sealed owned dispositions for all three operations; all
four generation/admission and generation/guard half-pairs reject before delegate or generic effects. Keyworded
remove covers UNMANAGED, HANDLED, OWNED_PRESERVED, and OWNED_ERROR; only the explicit positional call after exact
UNMANAGED reaches the legacy remove hook and recursive deletion.

The bounded-error proof now requires the exact AidtWorkspaceOperationError type, exact allowlisted category, and
canonical identifier ref. It excludes the complete resolved managed path, manager root, owned fixture root, and
temporary root, plus three distinct hostile relative components for the managed parent, generic manager root, and
owned fixture root. This closes both absolute and unique relative spellings without freezing an unrequired exact
message. The sealed DelegateResult protocol supplies no nested exception or arbitrary value for owned states, so
the suite correctly avoids a vacuous out-of-protocol exception fake.

The external-operation sentinels are credible and correctly scoped: every public case installs fail-fast spies for
workspace subprocess and Git execution, socket connection creation, Jira intake, and backend construction, then
asserts the shared call list is empty. Intended generic hooks are replaced by an ordered local filesystem spy, so
UNMANAGED lifecycle proof does not invoke a shell or weaken the external boundary.

Fresh command evidence:

- collection: 20/61 collected, 41 deselected, 0.26s;
- native focused RED: 20 failed, 41 deselected, 0.33s, with the failure distribution above;
- named positional controls: 3 passed in 0.31s;
- complete legacy non-delegate slice: 40 passed, 1 skipped, 20 deselected in 35.21s;
- Ruff via the repository virtual environment: All checks passed;
- Pyright: 0 errors, 0 warnings, 0 informations;
- delegate AST scan: 14 functions, maximum 49 lines, maximum nesting 2, no missing, size, or nesting violations;
- repository diff whitespace check: exit 0 with no output;
- no-index checks for the test and both workspace audit documents: expected content-difference exit 1 with no
  whitespace output.

No product or test file was edited by this audit. No live AIDT checkout, Git mutation, network, Jira, backend,
completion authority, commit, merge, push, or deployment was used. The temporary audit plugin was removed and left
no filesystem or repository artifact.
