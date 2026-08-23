# Windows managed-service stop implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Make plain Windows managed-service stop complete safely after its
grace period without assuming the recorded launcher PID serves the API.

**Architecture:** Generate a random per-launch service-instance ID, pass it
only to the detached child environment, persist it as an optional record field,
and consume it into orchestrator health state. After the bounded normal attempt,
probe every token-bearing plain Windows record regardless of launcher liveness;
an exact workflow and instance match authorizes force only against the health-
reported serving PID. Wait for both that PID and the recorded launcher to exit.
Explicit `--force` continues to control backend-process sweeping.

**Tech Stack:** Python 3.12+, pytest, Windows process/service helpers.

---

### Task 1: Lock the escalation contract with tests

**Files:**

- Modify: `tests/test_service.py`

**Step 1: Add the failing exact-workflow test**

Add `test_windows_service_stop_escalates_exact_instance_after_timeout`:

- save a record for launcher PID 1234 and instance `instance-a`;
- simulate Windows with launcher 1234 and serving PID 5678 alive;
- record `terminate_process` calls, removing serving PID 5678 on `force=True`
  and then allowing launcher 1234 to exit;
- let timeout zero observe the live set;
- make the ownership probe return a serving identity only for the record's
  exact host, port, workflow path, and service-instance ID;
- call plain `service stop` without `--force`;
- expect exit 0, termination calls `[(1234, False), (5678, True)]`, and a
  cleared record. Prove automatic force never targets a reused launcher PID.

Run the single test and verify it fails because current code never makes the
forced call without `args.force`.

**Step 2: Add the fail-closed characterization test**

Add `test_windows_service_stop_does_not_force_unverified_workflow` with the
same live PID but a false ownership probe. Expect exit 1, only the non-forced
termination call, and a retained record.

Add producer/consumer and persistence contract tests:

- new and legacy service-record JSON round trips;
- unique token generation and inherited-environment overwrite in the copied
  `_popen_detached` environment;
- direct and HTTP health expose the consumed instance ID and real process PID;
- exact workflow/instance with a positive serving PID succeeds even when the
  serving PID differs from the recorded launcher;
- missing, empty, malformed, or mismatched instance IDs and Boolean/string/
  float/non-positive serving PIDs fail closed;
- foreground health is nullable and the instance env var is removed before
  any backend/hook subprocess can inherit it;
- existing three-argument workflow-only callers remain backward compatible.

Add `test_windows_service_stop_keeps_record_when_forced_taskkill_is_denied`:
authorize escalation, make both taskkill attempts return false and PID remain
live, then assert exit 1 and retained record.

Add a POSIX characterization proving plain stop never auto-forces after its
timeout.

Add race tests for the serving PID exiting after proof and the launcher exiting
during a failed probe. Neither may cause an unrelated forced call. A failed
proof always retains a token-bearing record, including when the launcher is
already absent.

### Task 2: Implement the smallest Windows-only orchestration change

**Files:**

- Modify: `src/symphony/orchestrator/core.py`
- Modify: `src/symphony/service.py`

**Step 1: Authorize escalation explicitly**

Generate `secrets.token_urlsafe(32)` before spawn. Extend `_popen_detached` with
copied-environment overrides and overwrite `SYMPHONY_SERVICE_INSTANCE_ID`
without mutating the parent environment. Persist the ID as an optional strict
string in `ServiceRecord`; missing legacy values load as `None` and never
authorize automatic force.

At orchestrator construction, pop that env var into private state so hooks and
backends cannot inherit it. Add nullable `service_instance_id` plus positive
`orchestrator_pid: os.getpid()` to health.

Add a strict exact-service identity probe that returns the health-reported PID
only when workflow and instance ID match. Reject malformed IDs and malformed
PIDs. Preserve the legacy workflow-only Boolean probe.

In `_stop`, keep the bounded normal launcher attempt when the launcher is live.
For every token-bearing plain Windows record, probe afterward regardless of
whether that attempt timed out, made the launcher exit, or found it already
absent:

```python
if _IS_WIN32 and not args.force and record.service_instance_id:
    identity = probe_service_identity(...)
    proved_target = identity.orchestrator_pid if identity else None
```

For automatic Windows escalation, recheck the proved serving PID immediately,
force only that PID, and confirm both serving PID and launcher PID exit. Never
automatically force the recorded launcher. Preserve the explicit `--force`
root target and keep the later active-backend sweep guarded by `args.force`
exactly as today.

**Step 2: Run red tests to green**

Run all new service/health tests, then all `tests/test_service.py` and
`tests/test_orchestrator_health.py` tests.

**Step 3: Run process-lifecycle regression tests**

Run `tests/test_backends_lifecycle.py`, `tests/test_shell.py`,
`tests/test_projects.py`, and relevant service/project integration files.

### Task 3: Verify, review, commit, and push only on green

**Files:**

- Update: `docs/changelog/2026-08/23-windows-service-stop-fix/delivery-proof.md`
- Update the prior E2E report with post-fix results.

**Step 1:** Run Ruff, Pyright, `git diff --check`, and scratch doctor.

**Step 2:** Run the full pytest suite detached and outside the restrictive
sandbox with external basetemp; require zero failures, allowing only declared
capability skips.

**Step 3:** Repeat native browser E2E and orchestrator E2E suites.

**Step 4:** Start the isolated managed service outside the sandbox and prove
plain `service stop` exits 0, clears its record, removes the PID, and closes the
port.

**Step 5:** Run independent spec, quality, security/adversarial, and final diff
reviews; resolve every Critical or Important finding and re-review.

**Step 6:** Run the commit gate. If every required gate is green, commit on
`dev` using the repository's commit policy, fetch and verify `origin/dev` still
has the expected ancestor, then push a normal fast-forward. Never force-push.
