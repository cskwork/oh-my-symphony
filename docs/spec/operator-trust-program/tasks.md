# Tasks: Operator Trust Program

Statuses audited 2026-07-03 against `dev` (`7be48e2`); evidence in
`audit.md`. Detailed execution steps for the open tasks live in
`docs/plans/2026-07-03-operator-trust-implementation.md`.

- [x] 1. Re-audit current trust and reliability state
- [x] 1.1 Map current implementation against this spec
  - Done: `docs/spec/operator-trust-program/audit.md` records requirement
    status with file/test evidence. Full suite on `dev`: 942 passed, 2 skipped.
  - _Requirements: 4.6, 7.1_

- [ ] 2. Health Snapshot completion
- [x] 2.1 Health derivation tests for healthy, tick-error degraded, and registry degraded states
  - Done in `tests/test_orchestrator_health.py` (landed with `1818d60`).
  - _Requirements: 1.1, 1.2, 1.3_
- [x] 2.2 Shared Health Snapshot fields in `Orchestrator.health()`, `/api/v1/health`, state snapshot
  - Done in `src/symphony/orchestrator/core.py:609`, server route, `_health_summary`.
  - _Requirements: 1.1, 1.2, 1.3, 7.2_
- [ ] 2.3 Add `starting` status and `workflow_path` (additive)
  - `health()` returns `status: "starting"` before the first completed tick
    when nothing is degraded; add `workflow_path` to the payload.
  - Files: `src/symphony/orchestrator/core.py`, `tests/test_orchestrator_health.py`.
  - Tests: startup-pending test fails before implementation.
  - _Requirements: 1.4_
- [ ] 2.4 Owner-aware port messaging on startup and in doctor
  - Consult the service record (`requested_port`/`recorded_port` in
    `src/symphony/service.py`) when a bind fails; keep the current actionable
    fallback when the owner is unknown.
  - Files: `src/symphony/cli/main.py`, `src/symphony/cli/doctor.py`.
  - Tests: busy own-service port and unknown busy port cases.
  - _Requirements: 1.5, 5.2_

- [ ] 3. Attention Signals
- [x] 3.1 `budget_exhausted` signal, board/detail payloads, web badge with fallback
  - Done: `core.py:735`, `webapi.py:371`/`445`, `buildAttentionBadge` in
    `web/static/app.js`; tests in `test_orchestrator_dispatch.py` and
    `test_webapi.py`.
  - _Requirements: 2.1, part of 2.6_
- [ ] 3.2 Add remaining signal kinds with deterministic priority
  - Add `retry_scheduled` (from `_retry` entries, with due time),
    `stalled` (from stall/force-eject state), `lease_blocked` (from
    `lease_lost` / active-lease conflicts), `tracker_error` (from per-issue
    tracker failures). Priority pinned by tests:
    `stalled > lease_blocked > budget_exhausted > tracker_error > retry_scheduled`.
    Add `severity` and `due_at` additively; clear signals for terminal tickets.
  - Files: `src/symphony/orchestrator/core.py`, `tests/test_orchestrator_dispatch.py`, `tests/test_webapi.py`.
  - Tests: one test per kind plus a multiple-cause priority test; all fail
    before implementation.
  - _Requirements: 2.2, 2.3, 2.4, 2.5, 7.2_
- [ ] 3.3 Render Attention Signals in the TUI
  - Web already renders badges; add card/detail text in the TUI with a
    readable fallback for unknown kinds.
  - Files: TUI card/detail files under `src/symphony/tui/`, TUI tests.
  - Tests: TUI render tests where existing fixtures allow.
  - _Requirements: 2.6, 7.3_

- [ ] 4. Run History reader (not started)
- [ ] 4.1 Add RunRegistry history query
  - Bounded recent-run lookup with optional issue filter; clamp limit.
    No schema change: `status` doubles as terminal cause.
  - Files: `src/symphony/orchestrator/run_registry.py`, `tests/test_run_registry.py`.
  - Tests: empty history, issue filter, bounded/clamped limit, terminal-cause row shape.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
- [ ] 4.2 Add Run History API and CLI
  - Add `GET /api/v1/runs?issue=&limit=` and `symphony runs [--issue ID] [--limit N]`.
  - Files: `src/symphony/webapi.py`, `src/symphony/cli/main.py`, tests for both paths.
  - Tests: API contract tests and CLI routing/output tests.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
- [ ] 4.3 Add web drawer history section
  - Render recent attempts in the issue drawer without blocking board load.
  - Files: `src/symphony/web/static/app.js`, `src/symphony/web/static/style.css`.
  - Tests: static contract tests for history section strings and API use.
  - _Requirements: 3.1, 3.2, 3.4, 7.3_

- [x] 5. Backend Lifecycle Cleanup
- [x] 5.1 Process-group spawn and bounded termination
  - Done: all five backends spawn with `start_new_session` on POSIX;
    `terminate_process_tree` escalates SIGTERM to SIGKILL; no raw
    `proc.wait()` in backends. `tests/test_backends_lifecycle.py` passes.
  - _Requirements: 4.1, 4.2, 7.4_
- [x] 5.2 EOF and malformed-stream failure classification
  - Done: Codex EOF fails the turn; malformed-line streaks fail claude_code,
    codex, pi; gemini fails immediately. Accepted deviation: OpenCode skips
    malformed lines without a streak limit (bounded by per-turn process exit);
    recorded in `audit.md`.
  - _Requirements: 4.3, 4.4, 7.4_
- [x] 5.3 Force-eject process-group cleanup
  - Done: `_force_eject_zombie` (`core.py:3326`) kills the recorded process
    group before retry scheduling.
  - _Requirements: 4.5, 7.4_

- [ ] 6. Doctor v2 and Smoke Check
- [ ] 6.1 Expand doctor checks
  - Missing prompt files already fail config load with an actionable error
    (`coercion.py:117`); add a doctor visibility row listing resolved
    `prompts.base`/`prompts.stages` paths, and the owner-aware port fail
    message from task 2.4. Existing checks (agent CLI, pi auth, hook,
    workspace root, tracker, board viewer, shell) stay.
  - Files: `src/symphony/cli/doctor.py`, `tests/test_doctor.py`.
  - Tests: prompt row with/without stage templates, own-service port,
    unknown busy port; missing prompt file surfaces the config load error
    through doctor without a traceback.
  - _Requirements: 5.1, 5.2, 5.3_
- [ ] 6.2 Extend the existing smoke script
  - `scripts/smoke_web_api.py` already exercises issue CRUD/refresh/workflow;
    add a `/api/v1/health` check (accept `ok`/`starting`, report `degraded`
    reasons) and a next-diagnostic-step hint on failure.
  - Files: `scripts/smoke_web_api.py`, `tests/test_web_api_smoke_script.py`.
  - Tests: smoke passes against a test server and failure output names
    endpoint, status, and next step.
  - _Requirements: 5.4, 5.5_

- [ ] 7. Fresh-Clone Quickstart and examples
- [ ] 7.1 Document the proof path in both READMEs
  - README already leads with the file tracker and documents doctor and
    `/api/v1/health`; add the smoke script and `symphony runs` (after task 4)
    as proof steps in `README.md` and `README.ko.md`.
  - Tests: doc grep for key commands; no stale lane claims.
  - _Requirements: 6.1, 6.2, 6.3, 6.5_
- [ ] 7.2 Confirm examples and skill references
  - One confirm pass that examples use the four active lanes and advanced
    trackers stay credentialed secondary paths.
  - Files: `examples/*`, `skills/using-symphony/*`, related docs.
  - Tests: prompt/workflow render smoke and doc reference grep.
  - _Requirements: 6.2, 6.4_

- [ ] 8. Final verification and changelog
- [ ] 8.1 Run focused and full verification
  - Focused tests per slice, then `python -m pytest -q` via the project venv.
  - `symphony doctor ./WORKFLOW.md`; if `9999` is busy, verify ownership via
    the service record or rerun on an alternate port.
  - One real local service smoke against a file board.
  - _Requirements: 7.4, 7.5_
- [ ] 8.2 Record decisions and rejected alternatives
  - Append implementation decisions, rejected alternatives, and verification
    evidence to the date changelog.
  - Files: `docs/changelog/changelog-YYYY-MM-DD.md` for the completion date.
  - _Requirements: 7.5_
