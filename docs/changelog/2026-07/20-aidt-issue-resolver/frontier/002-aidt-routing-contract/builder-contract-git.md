# Builder A - contract and immutable Git objects

## Outcome

Implemented the iteration-2 closed routing contract and immutable Git-object observation boundary. Routing catalog
evidence is now read only from regular blobs in the fixed local
`refs/remotes/origin/aidt-prd` commit. The current branch, index, working tree, untracked files, ignored files, and
working-tree-only symlinks are neither evidence nor mutation targets.

The real-world mapping is: workflow configuration names a bounded service catalog; repository observation proves the
exact production-base object that owns each configured marker/anchor; later decision/storage/runtime layers can use
the resulting revision and repository-binding digest without guessing from the operator's checkout state.

## Theory and decisions

- Problem: the iteration-1 reader coupled ownership to `HEAD`, clean status, the index, and mutable working-tree
  bytes. This rejected valid dirty AIDT checkouts and could not hand Frontier 003 the production-base revision.
- Goal: return canonical settings and decoded scoring contents bound to one SHA-1 commit, while preserving every
  byte of user state and failing closed on repository, protocol, identity, ref, or object uncertainty.
- Contract boundary: `contract.py` has no filesystem, subprocess, card, tracker, or runtime imports. Absent/exactly
  disabled configuration returns before validating siblings. Enabled configuration is recursively closed and
  collision/cap checked.
- Git boundary: fixed argv and an allowlisted environment remove inherited `GIT_*` behavior, disable replace
  objects, optional locks, prompts, protocols, and lazy fetch. No status, diff, ls-files, hash-object, fetch, or
  working-tree opener is used.
- Runner boundary: stdout/stderr are drained concurrently. Each reader requests at most the channel's remaining
  allowance plus one byte, stores at most the cap, kills on the first crossing byte, and the caller always reaps the
  process before returning a structured result.
- Repository boundary: support only a non-symlink `.git` directory or bounded regular `gitdir:` file. Root,
  checkout, Git entry/directory, common directory, and object directory are identity-bound and rechecked. Alternates,
  replace refs, promisor metadata, and symlinked trust roots fail closed.
- Object boundary: exact scalar grammar; exactly one `ls-tree -z` regular-blob record for the requested ASCII path;
  strict UTF-8 blob decoding after the command cap; per-service and whole-observation decoded-byte caps.
- Durable handoff: `aidt-git-object-v1`, fixed ref, commit, canonical service/checkout, and opaque identity tokens
  produce a path-free `repository_binding_digest`.

Rejected alternatives:

- migrating the flat prototype's `HEAD`/status/working-tree reader;
- checking output length only after `subprocess.run(..., capture_output=True)` buffered it;
- allowing repository alternates, replacement refs, promisor lazy fetch, or inherited Git object/config overrides;
- exposing paths, argv, bytes, exception strings, or blocked card identifiers in public result reprs.

## TDD evidence

Initial contract red:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py -x
exit 2
ModuleNotFoundError: No module named 'symphony.aidt_routing.contract'; 'symphony.aidt_routing' is not a package
```

The coordinator then authorized the minimal contract-only package facade required for an importable vertical slice.
The iteration-1 flat prototype remains untouched as migration input; final integration must remove it before final
verification and add the planned runtime facade exports.

First contract green:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py -x
1 passed in 0.16s
```

Initial Git red:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_git_objects.py -x
exit 2
ModuleNotFoundError: No module named 'symphony.aidt_routing.git_objects'
```

Trusted intermediate green after fixed-ref, dirty-state, metadata, drift, sanitizer, and cap cases:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py -x
55 passed in 32.79s
```

Additional hostile cases then covered packed replace refs, SHA-256/top-level rejection, object-directory symlinks,
promisor metadata, missing required objects, malformed injected results, and bounded metadata reads.

Final verification:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py
63 passed in 31.81s

rtk ../../.venv/bin/ruff check --no-cache src/symphony/aidt_routing/__init__.py src/symphony/aidt_routing/contract.py src/symphony/aidt_routing/git_objects.py tests/aidt_routing_support.py tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py
All checks passed!

rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/aidt_routing/contract.py src/symphony/aidt_routing/git_objects.py
0 errors, 0 warnings, 0 informations

rtk git diff --check
exit 0
```

An AST gate over every owned product/test/support file found no function longer than 50 lines and no control-flow
nesting deeper than four. `git diff --no-index --check` over the new Git-object module emitted no whitespace finding;
its exit 1 is the expected no-index difference status for an untracked new file.

## Sanitized status follow-up

Runtime review found that `AidtRoutingResult.status` was repr-visible without an allowlist. The follow-up regression
first proved a hostile value such as `SECRET-SOURCE-/private/path` remained in the result:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py -k hostile_status -x
exit 1: expected "failure", got "SECRET-SOURCE-/private/path"
```

`AidtRoutingResult.__post_init__` now preserves only the runtime/core statuses `disabled`, `success`, `review`, and
`failure`; every other value becomes `failure` before repr or health consumption. The regression also proves every
allowlisted status remains unchanged and hostile source/path text is absent from repr.

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py -k hostile_status -x
1 passed, 24 deselected in 0.12s

rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py
64 passed in 24.99s

rtk ../../.venv/bin/ruff check --no-cache src/symphony/aidt_routing/contract.py tests/test_aidt_routing_contract.py
All checks passed!

rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/aidt_routing/contract.py
0 errors, 0 warnings, 0 informations

rtk git diff --check
exit 0
```

The follow-up AST gate reported no function over 50 lines and no control-flow nesting over four in the two changed
files.

## API handoff

Contract values:

- `load_routing_settings(ServiceConfig) -> RoutingSettings | None`
- `canonical_fingerprint(schema, value) -> lowercase SHA-256`
- `RoutingSettings.services: tuple[RoutingService, ...]`, sorted by canonical service ID
- `AidtRoutingFailure.category` plus sanitized optional `.identifier`
- immutable `AidtRoutingResult` with a repr that reports only statuses/categories/allowed refs/counts

Git runner:

```python
GitRunner = Callable[
    [tuple[str, ...], Mapping[str, str], float, int, int],
    GitCommandResult,
]
```

The five arguments are fixed argv, sanitized environment, timeout, stdout cap, and stderr cap.
`GitCommandResult` contains integer `returncode`, raw `stdout`/`stderr` bytes, and `timed_out`, `stdout_overflow`, and
`stderr_overflow` booleans.

Observation:

```python
observe_catalog(settings, *, git_runner=None, identity_probe=None) -> CatalogObservation
recheck_catalog(observation, *, git_runner=None, identity_probe=None) -> None
```

`CatalogObservation` exposes `services`, `trust_schema`, and `total_object_bytes`. Each `ObservedService` exposes
`service`, fixed `revision_ref`, `checkout_revision` (plus `revision` compatibility property),
`repository_binding_digest`, and decoded scoring `contents`. Private object/repository members exist only for
precommit recheck. `tests.aidt_routing_support.catalog_observation` builds pure no-filesystem decision fixtures.

## Files and integration notes

- Product: `src/symphony/aidt_routing/contract.py`, `src/symphony/aidt_routing/git_objects.py`.
- Tests/support: `tests/aidt_routing_support.py`, `tests/test_aidt_routing_contract.py`,
  `tests/test_aidt_routing_git_objects.py`.
- Scope-corrected seam: minimal `src/symphony/aidt_routing/__init__.py`, contract exports only.
- Final integration owner must export `filter_routing_candidates` and `run_aidt_routing` from runtime and delete the
  old flat `src/symphony/aidt_routing.py` only after all migration consumers have moved.
- No network, fetch, real AIDT repository, worktree provisioning, card persistence, or activation was performed.
