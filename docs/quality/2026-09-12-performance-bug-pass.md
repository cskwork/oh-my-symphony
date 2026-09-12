# Performance and bug pass, 2026-09-12

Starting point: `origin/dev` at `c89d04924c97922f91c537ba74115e5223cce4cd`.
The user subsequently authorized integrating `feat/sdlc-cycle-hardening` at `74d9d83`, merged locally as `0bfb65d`. This report covers that integrated state plus the fixes below. This is a bounded source and regression review, not a claim that the repository has no other defects.

## Confirmed changes

- File-board scans and dependency edits previously raised `RecursionError` on a 2,001-ticket dependency chain. Both cycle traversals now use explicit stacks. Acyclic chains succeed; cyclic chains retain the closed-path diagnostic. Four new tests failed before the fix and passed afterward.
- Board topological ordering previously scanned every remaining ticket once per dependency wave. It now visits reverse dependency edges as their blockers are removed. Sorted waves, duplicate-edge handling, dangling targets, and sorted cyclic leftovers retain their previous behavior.
- Per-turn backends previously reaped only cancellation failures. A prompt-write failure or stream-adapter exception could leave the owned subprocess alive until an external caller stopped the backend. Prompt-write and turn-processing failures now reap a still-live child before returning the error. Callback and watcher setup before the protected block were not changed. Two regression tests reproduced the missing cleanup before the fix.

The quality-pass fixes add no configuration, public API, dependency, schema, security policy, or user-board changes. The separately authorized feature branch carries its own documented 0.24.0 workflow and API additions.

## Integrated feature review

Reviewed the ten feature commits from `3f71d30` through `74d9d83`: current-branch release binding, deep/terminal mechanical gates, Done reopen budgets, intent tripwires, binary preflight, quota fallback, gate statistics and prompt input, SDLC metadata, release-gate extraction, and 0.24.0 release notes. Review followed the contract evaluation and transition callers, persisted run-count lookup, fallback pin/retry flow, release authority checks, and stats-to-browser path.

Two additional integration defects were reproduced and fixed:

- Generic `limit reached` / `limit exceeded` quota markers also matched transient rate-limit failures, causing an unnecessary backend switch. Transient markers now take priority over generic limit wording. Explicit quota/usage/billing markers still trigger fallback, including a message that also contains HTTP 429 and rate-limit wording. The two added transient variants failed before the fix; the focused quota/fallback/reopen run passed 23 tests afterward.
- The stats page displayed "no activity" when the history contained only gate/rewind events, hiding the newly added Gates card. Its empty-state predicate now recognizes numeric gate counts. A real Chromium test seeded gate-only history through the stats store and failed before the fix, then passed after it. The same test verifies recurring misses, intent tripwire details, and automatic micro-to-full track display.

## Inspection coverage

| Area | Evidence inspected |
| --- | --- |
| Orchestration | Scheduler dependency analysis, core full-ticket lookup callers, worker-exit ownership and lease handling |
| Trackers | File scan/hydration and edit validation, atomic ticket writes, external HTTP retry helper |
| Web API | JSON input/error handling, board/detail reads and issue-patch validation with thread offloading |
| Persistence | Atomic JSON temporary-file cleanup and rename retries, SQLite registry connection/WAL setup, stats append/read failure handling |
| Backends | Shared per-turn spawn/write/stream/reap lifecycle and persistent Codex shutdown |
| Delivery checks | CI workflow, CONTRIBUTING.md, project doctor, lint, translation and type checks |

Inspection alone does not establish runtime correctness for unchanged paths. No additional edits were justified by this bounded review.

## Regression evidence

- Before graph fix: `pytest -q tests/test_tracker_validate.py` reported 4 failed, 12 passed. All four failures were `RecursionError` on deep acyclic/cyclic chains.
- Before backend fix: `pytest -q tests/test_backends_lifecycle.py -k per_turn_failure` reported 2 failed. Both expected cleanup calls were absent.
- After fixes: `pytest -q tests/test_backends_lifecycle.py tests/test_backend_contract.py tests/test_tracker_validate.py` reported 112 passed, 3 skipped in 12.25 seconds.
- Compared against the starting revision on 1,000 seeded branching/cyclic graphs: exact topological-order and cycle-diagnostic parity. Edited-node cycle traversal also matched for all 20,000 node queries.
- Manual smoke with a real local Python child that sleeps: induced stdin and stream failures both left the OS PID absent (`os.kill(pid, 0)` raised `ProcessLookupError`), preserved the original exception object, and cleared the backend PID. Exit 0; PIDs 74086 and 74088. Command: `PYTHONPATH=src:. .venv/bin/python /private/tmp/symphony-quality-process-smoke.py`. The scratch script subclasses the shared per-turn backend and injects failures immediately after spawning, without invoking an agent CLI.
- Integrated contracts, contract transitions, release contracts, stats, chat intent, workflow and deep-preset E2E tests: 396 passed in 155.21 seconds.
- Existing browser E2E cases: all 5 passed. The added gate-only stats and intent-tripwire browser case passed separately in 2.53 seconds. The first browser run also provided the failing stats-rendering regression. Browser fixtures use temporary boards and a fake chat backend with an ephemeral local HTTP server; no real provider runs or user-board actions occur.
- Integrated Ruff passed. Translation check passed for 575 keys in English and Korean. Pyright reported 0 errors and 0 warnings.

## Performance measurement

Local macOS arm64, repository `.venv/bin/python`; median of three calls using a synthetic single dependency chain. Timings measure `topological_order`, excluding graph construction and disk I/O.

| Tickets | Before | After |
| --- | --- | --- |
| 1,000 | 0.04743 s | 0.000938 s |
| 5,000 | 1.11580 s | 0.004032 s |

These are graph-function measurements, not end-to-end board/API latency claims. Reproduce on either revision:

```python
from statistics import median
from time import perf_counter
from symphony.trackers.validate import topological_order

for count in (1000, 5000):
    edges = {str(i): (str(i - 1),) if i else () for i in range(count)}
    samples = []
    for _ in range(3):
        started = perf_counter()
        topological_order(edges)
        samples.append(perf_counter() - started)
    print(count, median(samples))
```

## Limits

The production workflow doctor was run without dispatching workers. Initial sandboxed workspace-root and Git-object write probes were denied. The coordinator repeated doctor with the required filesystem access: both write checks passed, but doctor still exited 1 because this is a protected source repository and port 9999 is occupied by Python PID 72744. The running server was left untouched. Doctor also warned that bare `symphony` is absent from the login-shell PATH. Board dependencies and schema checks passed.

No real provider CLI sessions, external tracker mutations, or user-board mutations were performed. Browser interactions were confined to isolated test fixtures. Backend lifecycle evidence uses local tests, controlled subprocess doubles, and the real local Python-child cleanup smoke. Provider authentication and live network behavior remain unverified.

The pre-integration sandboxed baseline completed with 2 failed, 2,510 passed, and 14 skipped in 430.36 seconds. The failures were process identity and orphan-process recovery checks: process identity was unavailable under the sandbox, and the recovery path correctly refused to kill an ambiguously identified process. The final integrated coverage run used the required local process-inspection access and passed:

```text
.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80
2561 passed, 15 skipped in 471.72s
Total coverage: 85.44% (required: 80%)
Exit: 0
```

The two previously failing process-identity/recovery tests passed. Browser tests remain opt-in in this standard CI command; their separate results are recorded above. Final `git diff --check` passed. No remote delivery claim is made here; the coordinator owns PR, CI and merge verification.
