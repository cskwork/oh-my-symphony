# Frontier 003 Git-state repair report

Date: 2026-07-21

## Verdict

PASS.

All five MUST and two SHOULD findings in `git-state-verifier-report.md` are repaired through the public Git-state
interfaces. The focused suite passes 62 tests, and the requested 298-test pre-provisioner baseline—expanded by the
landed manifest-helper and repair regressions—passes as a 349-test superset. No live repository, network, Jira, AIDT
checkout, commit, or product mutation outside temporary pytest repositories was used.

## Repairs

### MUST-1 - Exact remote-tracking feature-ref collision

`classify_target_artifacts` now returns `AMBIGUOUS` when either
`refs/remotes/origin/<derived-branch>` or the same exact branch under another one-component remote exists. Repository
snapshot construction rejects that collision. It cannot be mistaken for an absent target before create or prepared
recovery.

### MUST-2 - Locked registrations are not exact phase targets

The shared target-registration proof now requires `locked=False`. Both S1 -> S2 create proof and cleanup-pre ->
cleanup-post removal proof reject an otherwise exact locked path/branch/SHA registration.

### MUST-3 - Command-specific parser grammar

- Type-1 status accepts only non-conflict, non-rename tracked XY pairs.
- Type-2 requires an `R`/`C` XY marker and an exact three-digit `R|C` score from `000` through `100`.
- Type-u accepts only the seven Git unmerged XY pairs.
- `?` and `!` remain exclusive record types and cannot appear in type-1/type-2/type-u XY.
- Submodule state is exact `N...` or `S<c><m><u>` and agrees with exact Gitlink modes.
- Modes are limited to Git's relevant exact wire values: `000000`, `040000`, `100644`, `100755`, `120000`, and
  `160000`.
- Full refs enforce the installed Git `check-ref-format` restrictions for dot-prefixed components, `.lock`, `..`,
  `@{`, forbidden/control characters, empty/repeated components, leading/trailing slash, and trailing dot.

The verifier's `refs/heads/@` example was adjudicated against real Git before parser edits:

```text
rtk git check-ref-format refs/heads/@
```

Git 2.53.0 returned exit 0. Per the parent ruling, `refs/heads/@` remains valid; the single-ref `@` restriction does
not apply to that full ref. A bounded differential fixture now requires representative accepted and rejected refs to
agree with the installed `git check-ref-format` before asserting the product parser result. This ruling supersedes
only that verifier example, not the other MUST-3 restrictions.

### MUST-4 - Raw and decoded origin controls

Origin validation rejects raw and percent-decoded C0/DEL bytes in repository paths and in the otherwise permitted
SSH username. Malformed percent escapes, percent-encoded separators, decoded backslashes, and decoded dot segments
remain rejected. Normal accepted HTTPS/SSH digests are unchanged.

### MUST-5 - No-follow ignored-directory proof

Each ignored directory is opened with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`, compared across pathname `lstat`, descriptor
`fstat`, and post-enumeration `lstat`, and enumerated through `os.scandir(fd)`. Directory identities are retained as
bounded witnesses and rechecked after the second status command. Executable fault fixtures reject replacement:

- immediately before descriptor open;
- during descriptor enumeration;
- immediately before the second status observation.

The status wire's trailing slash is used privately to require a directory at the descriptor seam; the public
`StatusEntry` API remains unchanged.

### SHOULD-1 - Cap-plus-one enumeration

Ignored-directory and hooks-directory scanners count as they iterate. They stop on the first entry beyond the
10,000-content or 2,500-hook cap before sorting or materializing any further entry. Sentinel iterators prove neither
scanner asks for an entry after the cap-plus-one witness.

### SHOULD-2 - Hooks-root symlink rejection

Repository preflight now `lstat`s the hooks root, returns only on exact `ENOENT`, and requires the root object itself
to be a directory. A symlink to an empty hooks directory fails before any worktree mutation.

## TDD evidence

The following regressions failed before their corresponding repair:

| Red command suffix | Observed red result |
|---|---|
| `::test_remote_tracking_feature_ref_is_an_ambiguous_target` | 2 failed: both remotes returned `ABSENT` |
| `::test_locked_target_is_rejected_by_create_and_remove_delta_proof` | 1 failed: locked S2 returned a digest |
| `::test_status_parser_rejects_impossible_command_specific_fields` | 1 failed: impossible status row accepted |
| `::test_ref_parser_matches_representative_git_check_ref_format` | 1 failed: dot-prefixed component accepted |
| `::test_origin_parser_rejects_raw_and_decoded_control_text` | 7 failed, 5 passed: encoded controls accepted |
| `::test_ignored_directory_replacement_before_descriptor_open_is_rejected` | 1 failed: no descriptor seam/no failure |
| `::test_ignored_directory_replacement_before_second_status_is_rejected` | 1 failed: inode replacement accepted |
| `::test_ignored_directory_cap_stops_at_cap_plus_one_before_materializing` | 1 failed with over-read sentinel |
| `::test_add_rejects_symlink_hooks_root_before_mutation` | 1 failed: empty symlink target accepted |
| `::test_hooks_cap_stops_at_cap_plus_one_before_materializing` | 1 failed with over-read sentinel |

Each command used this exact prefix:

```text
rtk env PYTHONPATH=src pytest -p no:cacheprovider -q tests/test_aidt_worktree_git_state.py
```

The during-enumeration fixture was added at the shared descriptor solution boundary and passed immediately; the
before-open red already proved the old pathname implementation lacked that boundary.

## Green verification

### Focused suite

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py
```

Result: `62 passed in 27.11s`.

### Pre-provisioner compatibility

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
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

Result: `349 passed in 87.97s`. This is the current collected superset of the requested 298-test baseline; no
provisioner/runtime draft suite was included.

### Static and structure gates

```text
rtk ../../.venv/bin/ruff check --no-cache \
  src/symphony/aidt_worktree/git_state.py \
  src/symphony/aidt_worktree/__init__.py \
  tests/test_aidt_worktree_git_state.py
```

Result: `All checks passed!`.

```text
rtk ../../.venv/bin/pyright \
  src/symphony/aidt_worktree/git_state.py \
  src/symphony/aidt_worktree/__init__.py
```

Result: `0 errors, 0 warnings, 0 informations`.

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py::test_git_state_product_functions_stay_bounded_and_shallow
```

Result: `1 passed in 0.13s`; every product function is at most 50 lines and control nesting is at most four.

### Lazy facade and whitespace

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_git_state.py::test_public_facade_keeps_git_state_lazy_until_requested
```

Result: `1 passed in 0.16s`. No facade edit was required.

`rtk git diff --check` returned exit 0 with no findings. Because the owned Git-state product and test remain new
untracked files in the shared worktree, `git diff --check --no-index /dev/null <file>` returned the expected content-
difference exit 1 with no whitespace output for each.

## Scope

Changed only:

- `src/symphony/aidt_worktree/git_state.py`
- `tests/test_aidt_worktree_git_state.py`
- this report

No provisioner, runtime, manifest, route, workspace, Core, other test, facade, live profile, branch deletion,
recursive deletion, commit, or network path was changed or invoked.
