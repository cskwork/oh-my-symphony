# AF-03 through AF-16 adversarial review

Role: fresh-context Adversarial Reviewer
Date: 2026-07-10
Worktree: `/private/tmp/symphony-supergoal-af-03-16`

## Decision

Reject the current run for verification. Four concrete lifecycle and ordering
defects remain: three High severity and one Medium severity. The focused changed-
module suite passes, but its tests do not exercise these failure interleavings.

## Concrete defects

### High — AF-10 makes an orphaned run redispatchable before killing its recorded agent

The frozen goal requires startup recovery to kill the recorded live process
group **before** the row becomes redispatchable. The implementation reverses
that order:

- `RunRegistry.reclaim_dead_owner_leases` changes every qualifying row from
  `active` to `orphaned` and commits at
  `src/symphony/orchestrator/run_registry.py:270-316`.
- Only after that method returns does `Orchestrator._ensure_run_registry` call
  `kill_process_group` at `src/symphony/orchestrator/core.py:762-789`.
- `RunRegistry.acquire_run` considers only active rows and can insert a new run
  at `src/symphony/orchestrator/run_registry.py:109-157`.

Therefore a second orchestrator can acquire and start the same issue after the
commit and before the old process group is killed. The old and new workers can
overlap against the same worktree. The new tests prove that one orchestrator
kills before `_ensure_run_registry` returns; they do not race reclaim against a
second acquisition.

Required correction: preserve an active/non-dispatchable recovery state until
process-group termination has completed, then publish the row as reclaimable in
an ordering that a concurrent acquirer cannot observe halfway through.

### High — AF-08 bounded stop abandons the worker without releasing its run lease

For a cancellation-resistant worker, `_drain_worker_tasks` waits, optionally
kills its recorded process group, and logs abandonment, but it neither removes
the owned entry nor calls `_finish_run_lease`
(`src/symphony/orchestrator/core.py:677-716`). `stop()` then clears `_running`
and closes the registry (`src/symphony/orchestrator/core.py:1111-1147`).

The active registry row can consequently survive until its TTL. This is
especially harmful for an in-process stop/restart: the owner PID is still live,
so dead-owner recovery deliberately refuses to reclaim it. The issue remains
lease-blocked even though Symphony has declared shutdown complete and discarded
its in-memory ownership record.

The AF-08 regression at `tests/test_dispatch_state.py:191-229` asserts only the
deadline, pause ordering, and empty `_running` mapping. It installs no registry
row and makes no terminal-status assertion, so the ownership leak is invisible.

Required correction: route every abandoned worker through an idempotent
force-eject ownership cleanup that terminalizes its run lease before the
registry is closed; add a cancellation-resistant regression that reopens the
database and asserts the row is not active.

### High — AF-06 startup sweep deletes unrelated stale `.tmp-*` files

`FileBoardTracker._sweep_stale_temps` scans the unrestricted pattern
`.tmp-*` and unlinks every old match
(`src/symphony/trackers/file.py:513-535`). AF-06 scopes cleanup to tracker-owned
atomic artifacts: legacy `.tmp-*.md` files and the new `.tmp-*.tmp` format. A
board root may contain an unrelated operator or tool file whose name begins
with `.tmp-`; constructing the tracker deletes it.

Focused reproduction created a two-minute-old `.tmp-operator-notes.txt`, then
constructed `FileBoardTracker`:

```text
stale_tracker_temp_swept ... path=.../.tmp-operator-notes.txt ...
unrelated_exists_after_init=False
```

This is destructive out-of-scope behavior, not merely a warning mismatch.

Required correction: restrict startup deletion to formats Symphony can prove it
owns, ideally by a tracker-specific filename grammar. Add negative tests for
stale `.tmp-*` files with unrelated suffixes.

### Medium — AF-09 marks the backend closed before teardown, making a failed reap non-retryable

On a corrupt stream, `_stdout_reader` sets `_closed = True` before awaiting
`terminate_process_tree` (`src/symphony/backends/codex.py:652-680`). If that
await raises or is cancelled, the process remains attached. A subsequent
explicit `stop()` returns immediately because `_closed` is already true
(`src/symphony/backends/codex.py:369-385`), so it neither retries termination
nor cancels the remaining reader task.

A focused probe made the termination helper raise once, caught the reader
error, and then called `stop()`:

```text
reader_error=synthetic teardown failure
closed=True
teardown_attempts=1
process_still_attached=True
process_returncode=None
```

The AF-09 regression at `tests/test_backends.py:2054-2102` stubs only successful
termination, so it cannot detect the poisoned closed state.

Required correction: separate "reject future turns" from "cleanup complete",
or make `stop()` retry process cleanup even when the logical client is closed.
Add a teardown-failure regression that proves a later stop reaps the process.

## Unproven risks, not promoted to findings

- AF-04's running-state check and tracker mutation are separate operations. The
  current test proves no partial mutation for a worker already present at the
  check, but it does not prove behavior if dispatch begins between the guard and
  `update_fields`. This needs an explicit concurrency decision or regression
  before claiming a global atomic guarantee.
- AF-10 persists only a numeric backend PID/process-group id. No process birth
  identity is checked before signalling, so PID reuse could target an unrelated
  process group. No deterministic reproduction was established in this review.

No additional concrete defect was found in the reviewed scheduler overlap and
latch behavior, exact-id duplicate determinism, configured custom-state order,
or lifetime turn-number boundaries.

## Verification performed

Focused changed-module regression:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backend_contract.py tests/test_backends.py tests/test_dispatch_state.py tests/test_orchestrator_continuous_improvement.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_prompt.py tests/test_tracker_file.py tests/test_webapi.py
```

Result: `489 passed, 2 skipped in 19.05s`.

The two destructive/lifecycle Python probes above completed successfully and
produced the quoted outputs. This role did not run the full repository suite;
the four findings block promotion regardless of the passing focused suite.

## Scope

This review read the frozen goal and plan, AF-03 through AF-16, all builder and
improver reports, `WORKFLOW.md`, and the complete current diff. It changed only
this review report. It did not edit production code, tests, tickets, `GOAL.md`,
`QA.md`, `run-state.json`, or the dated changelog, and it made no commit.
