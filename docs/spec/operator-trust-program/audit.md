# Audit: Operator Trust Program vs `dev` (2026-07-03)

This document completes task 1.1 of `tasks.md`: map the spec against the code
that is actually on `dev` (HEAD `7be48e2`). The spec was written alongside
commit `1818d60` ("feat: harden reliability lifecycle and trackers"), which
already landed most of Phase 1 health work and Phase 2 lifecycle work. Each
claim below cites a file location or a passing test.

Verification evidence for this audit:

- Focused suites pass on `dev`: `tests/test_orchestrator_health.py`,
  `tests/test_backends_lifecycle.py`, `tests/test_run_registry.py`,
  `tests/test_doctor.py`, `tests/test_web_api_smoke_script.py`,
  `tests/test_web_static_contract.py` — 48 passed.
- Full suite on `dev`: `python -m pytest -q` — 942 passed, 2 skipped
  (2026-07-03). The five known-red backend tests from the older
  `feat/reliability-hardening` WIP (`70dbc75`) do not exist on `dev`;
  `1818d60` superseded that work.

## Requirement status summary

| Requirement | Status | Remaining gap |
|---|---|---|
| 1. Health Snapshot | Mostly done | 1.4 startup-pending status; 1.5 owner-aware port message |
| 2. Attention Signals | Partial | Only `budget_exhausted` exists; no retry/stalled/lease/tracker kinds, no priority order, no TUI rendering |
| 3. Run History | Not implemented | No registry query, no `/api/v1/runs`, no `symphony runs`, no drawer section |
| 4. Backend Lifecycle Cleanup | Done (verify-only) | Record accepted deviation for OpenCode malformed handling |
| 5. Doctor v2 + Smoke Check | Partial | No prompt-file check; port check not owner-aware; smoke does not hit `/api/v1/health` |
| 6. Fresh-Clone Quickstart | Partial | Smoke script and run history not documented (run history does not exist yet) |
| 7. Implementation discipline | In progress | This audit satisfies 7.1; the rest applies per slice |

## Requirement 1: Health Snapshot

Already satisfied:

- 1.1 `Orchestrator.health()` exists (`src/symphony/orchestrator/core.py:609`),
  served by `GET /api/v1/health`, and `_health_summary` embeds it in the state
  snapshot (`tests/test_orchestrator_health.py::test_snapshot_includes_health_summary`).
- 1.2 Consecutive tick failures degrade with reason `tick_failures`
  (`test_health_degraded_after_consecutive_tick_failures`).
- 1.3 Registry errors degrade with reason `run_registry_error` without killing
  the API server; `health()` is counters-only by design (docstring at
  `core.py:609`).
- Edge case "missing workflow file / broken config": direct-run startup fails
  with actionable errors, not tracebacks (`src/symphony/cli/main.py:184-195`,
  commit `cb943a0`).

Gaps:

- 1.4 Before the first completed tick, `health()` reports `status: "ok"`
  (only `ok`/`degraded` exist). There is no startup-pending statement beyond
  `tick.last_completed_at: null`.
- 1.5 Port-busy at startup prints an actionable message
  (`cli/main.py:226-236`) but does not say whether the port belongs to this
  workflow's own service, even though the service record stores
  `requested_port`/`recorded_port` (`src/symphony/service.py:69-70`).

Spec drift (design.md was written ahead of reading the landed code):

- Implemented status values are `ok`/`degraded`, not
  `starting`/`healthy`/`degraded`/`unhealthy`.
- Implemented field names are `tick.last_completed_at`,
  `tick.consecutive_failures`, `run_registry.{enabled,error_count,last_error}`,
  `counts.{running,retrying}` — not `last_tick_at`,
  `consecutive_tick_errors`, `registry`.
- `workflow_path` is not in the payload.

Resolution: design.md data model is updated to the implemented shape; the
remaining plan adds `starting` and `workflow_path` additively. Renaming
`ok` to `healthy` is rejected under the compatibility NFR.

## Requirement 2: Attention Signals

Already satisfied:

- 2.1 `issue_attention` returns `budget_exhausted` with a concise message
  (`core.py:735-743`;
  `tests/test_orchestrator_dispatch.py::test_issue_attention_reports_budget_exhaustion`).
- Attention payloads ride on both board cards and issue detail
  (`src/symphony/webapi.py:371`, `webapi.py:445`;
  `tests/test_webapi.py::test_issue_detail_includes_attention`).
- Web renders a badge with a readable-text fallback
  (`buildAttentionBadge` in `src/symphony/web/static/app.js`).

Gaps:

- 2.2 retry-scheduled, 2.3 stalled, 2.4 lease-blocked, 2.5 tracker-error
  signals do not exist. The raw inputs already exist: `self._retry` entries,
  stall/force-eject state (`core.py:3326`), `lease_lost` on running entries,
  and tracker failure counters — they are just not folded into
  `issue_attention`.
- Multiple-cause priority order is undefined (single kind today).
- 2.6 TUI has no attention rendering at all (`grep attention src/symphony/tui/`
  is empty).

Spec drift: the implemented payload is `{kind, label, message}`; design.md
previously specified `{kind, severity, message, due_at, reason}`. Resolution:
keep `{kind, label, message}` as the stable base and add `severity` and
`due_at` additively.

## Requirement 3: Run History

Not implemented. Verified absences on `dev`:

- No recent-runs query on `RunRegistry` (methods are lease/flags-focused:
  `acquire_run`, `heartbeat`, `complete_run`, `has_active_lease`,
  `active_leases`, `expire_stale`, `reclaim_dead_owner_leases`, `get_run`,
  issue-flag accessors).
- No `/api/v1/runs` route, no `symphony runs` CLI token
  (`src/symphony/cli/main.py:341-360` routes board/doctor/service/tui only).
- No run-history section in the web drawer.

Schema note: the `runs` table already stores `run_id`, `issue_id`,
`identifier`, `status`, `attempt`, `attempt_kind`, `agent_kind`,
`workspace_path`, timestamps, and lease/owner columns. There is no separate
`error` column; terminal cause is carried in `status`
(e.g. `force_ejected_zombie`). The history row therefore derives its
error text from `status` instead of adding a column.

## Requirement 4: Backend Lifecycle Cleanup

Done; commit `1818d60` closed the gaps the older handoff
(`docs/plans/2026-07-02-reliability-handoff.md`) called R2/R7:

- 4.1 All five backends spawn with `start_new_session=os.name == "posix"`.
- 4.2 `terminate_process_tree` does SIGTERM, bounded wait, SIGKILL escalation
  (`src/symphony/_shell.py:189+`); no raw `proc.wait()` remains in
  `src/symphony/backends/` (all reap via `safe_proc_wait`).
- 4.3 Codex EOF before turn completion fails the turn with a classified
  failure (`src/symphony/backends/codex.py`, EOF handling near line 650).
- 4.4 Malformed-line streak limits exist in claude_code, codex, and pi;
  gemini fails immediately on malformed JSON.
- 4.5 Force-eject kills the recorded process group before retry scheduling
  (`core.py:3326-3363` `_force_eject_zombie` calls `kill_process_group`);
  the pid comes from `codex_app_server_pid` or `agent_pid` (`core.py:2723`).
- `tests/test_backends_lifecycle.py` passes on `dev`.

Accepted deviation (4.4): the OpenCode backend tolerantly skips malformed
JSON lines without a streak limit. This is bounded in practice because
`opencode run` is one process per turn and EOF ends the read loop; adding a
streak limit is optional symmetry work, not a correctness gap.

## Requirement 5: Doctor v2 and Smoke Check

Already satisfied:

- Doctor runs eight checks: port bind, agent CLI availability, pi auth,
  after-create hook, workspace root, tracker config, board viewer, shell
  (`src/symphony/cli/doctor.py:60-290`). 5.3's "cheap reliable probe only"
  matches the pi auth check behavior.
- A smoke script exists with tests: `scripts/smoke_web_api.py` exercises
  issue create/read/patch/delete, refresh, and workflow endpoints against a
  live server and cleans up after itself
  (`tests/test_web_api_smoke_script.py`).

Gaps:

- 5.1 Partially covered upstream: a missing configured prompt file already
  fails config load with `ConfigValidationError("prompt file not found")`
  (`src/symphony/workflow/coercion.py:117-121`), which doctor hits before its
  checks run. What is missing is a doctor row that lists the resolved
  `prompts.base`/`prompts.stages` paths so operators can see which templates
  are active.
- 5.2 `_bind_port` failure says `cannot bind host:port` without consulting
  the service record to report our-own-service ownership.
- 5.4 The smoke script never calls `/api/v1/health`.
- 5.5 Smoke failures print endpoint/status but no next diagnostic step.

## Requirement 6: Fresh-Clone Quickstart

Already satisfied (commits `6f93945`, `7be48e2`):

- README leads with the file-tracker "no agent CLI required" path and
  documents `symphony doctor`; `/api/v1/health` appears in the API table;
  README.ko mirrors structure.

Gaps:

- The smoke script is not documented as a proof step.
- Run history cannot be documented until Requirement 3 lands.
- Examples/lane alignment (6.4) needs one confirm pass per slice 7.2.

## Requirement 7: Implementation discipline

- 7.1 is satisfied by this document.
- 7.2-7.5 apply per remaining slice; see
  `docs/plans/2026-07-03-operator-trust-implementation.md`.
