# Tasks: Operator Trust Program

Statuses audited 2026-07-03 against `dev` (`7be48e2`); evidence in
`audit.md`. Detailed execution steps for the open tasks live in
`docs/plans/2026-07-03-operator-trust-implementation.md`.

- [x] 1. Re-audit current trust and reliability state
- [x] 1.1 Map current implementation against this spec
  - Done: `docs/spec/operator-trust-program/audit.md` records requirement
    status with file/test evidence. Full suite on `dev`: 942 passed, 2 skipped.
  - _Requirements: 4.6, 7.1_

- [x] 2. Health Snapshot completion
- [x] 2.1 Health derivation tests for healthy, tick-error degraded, and registry degraded states
  - Done in `tests/test_orchestrator_health.py` (landed with `1818d60`).
  - _Requirements: 1.1, 1.2, 1.3_
- [x] 2.2 Shared Health Snapshot fields in `Orchestrator.health()`, `/api/v1/health`, state snapshot
  - Done in `src/symphony/orchestrator/core.py:609`, server route, `_health_summary`.
  - _Requirements: 1.1, 1.2, 1.3, 7.2_
- [x] 2.3 Add `starting` status and `workflow_path` (additive)
  - Done: `health()` returns additive `status: "starting"` before the first
    completed tick when nothing is degraded, and includes `workflow_path`.
  - Evidence: `tests/test_orchestrator_health.py`; full suite
    `965 passed, 2 skipped`.
  - _Requirements: 1.4_
- [x] 2.4 Owner-aware port messaging on startup and in doctor
  - Done: startup and doctor bind failures consult the Symphony service record
    before falling back to generic process/port instructions.
  - Evidence: `tests/test_cli_run_startup.py`, `tests/test_doctor.py`; full
    suite `965 passed, 2 skipped`.
  - _Requirements: 1.5, 5.2_

- [x] 3. Attention Signals
- [x] 3.1 `budget_exhausted` signal, board/detail payloads, web badge with fallback
  - Done: `core.py:735`, `webapi.py:371`/`445`, `buildAttentionBadge` in
    `web/static/app.js`; tests in `test_orchestrator_dispatch.py` and
    `test_webapi.py`.
  - _Requirements: 2.1, part of 2.6_
- [x] 3.2 Add remaining signal kinds with deterministic priority
  - Done: added `retry_scheduled` (from `_retry` entries, with due time),
    `stalled` (from stall/force-eject state), `lease_blocked` (from
    `lease_lost` / active-lease conflicts), and `tracker_error` (from
    per-issue tracker failures). Priority pinned by tests:
    `stalled > lease_blocked > budget_exhausted > tracker_error > retry_scheduled`.
    `severity` and `due_at` are additive; terminal tickets suppress attention.
  - Evidence: `tests/test_orchestrator_dispatch.py`, `tests/test_webapi.py`;
    focused attention/UI batch `190 passed`.
  - _Requirements: 2.2, 2.3, 2.4, 2.5, 7.2_
- [x] 3.3 Render Attention Signals in the TUI
  - Done: TUI card, detail pane, and ticket modal render attention labels,
    messages, and due times with readable fallback labels.
  - Evidence: `tests/test_tui.py`; focused attention/UI batch `190 passed`.
  - _Requirements: 2.6, 7.3_

- [x] 4. Run History reader
- [x] 4.1 Add RunRegistry history query
  - Done: bounded recent-run lookup with optional issue/identifier filter and
    clamped limit. No schema change: `status` doubles as terminal cause.
  - Evidence: `tests/test_run_registry.py`; full suite `965 passed, 2 skipped`.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
- [x] 4.2 Add Run History API and CLI
  - Done: added `GET /api/v1/runs?issue=&limit=` and
    `symphony runs [--issue ID] [--limit N]`.
  - Evidence: `tests/test_webapi.py`, `tests/test_cli_main_routing.py`; live
    smoke confirmed `/api/v1/runs?limit=5` responds.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
- [x] 4.3 Add web drawer history section
  - Done: issue drawer lazy-loads recent attempts without blocking board load
    and renders loading, empty, unavailable, and row states.
  - Evidence: `tests/test_web_static_contract.py`; focused touched-slices
    batch `137 passed`.
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

- [x] 6. Doctor v2 and Smoke Check
- [x] 6.1 Expand doctor checks
  - Done: doctor lists resolved prompt paths and uses the owner-aware port
    failure path. Missing prompt files still fail config load before checks.
  - Evidence: `tests/test_doctor.py`; temp workflow doctor passed all required
    rows with only the legacy board-viewer warning.
  - _Requirements: 5.1, 5.2, 5.3_
- [x] 6.2 Extend the existing smoke script
  - Done: smoke checks `/api/v1/health`, accepts `ok`/`starting`, reports
    degraded reasons, and includes a next diagnostic step on failure.
  - Evidence: `tests/test_web_api_smoke_script.py`; live smoke passed all
    nine API/static/CRUD checks.
  - _Requirements: 5.4, 5.5_

- [x] 7. Fresh-Clone Quickstart and examples
- [x] 7.1 Document the proof path in both READMEs
  - Done: both READMEs include the prove-it path for server, health, run
    history, and smoke script.
  - Evidence: doc grep for `symphony runs`, `scripts/smoke_web_api.py`,
    `/api/v1/health`, and `/api/v1/runs?issue=&limit=`.
  - _Requirements: 6.1, 6.2, 6.3, 6.5_
- [x] 7.2 Confirm examples and skill references
  - Done: smoke workflow and `using-symphony` workflow snippets use the four
    active lanes; advanced tracker examples remain credentialed secondary
    paths.
  - Evidence: doc/reference grep over examples and skill docs.
  - _Requirements: 6.2, 6.4_

- [x] 8. Final verification and changelog
- [x] 8.1 Run focused and full verification
  - Done: focused slice tests, compile check, whitespace check, full suite,
    doctor, and one real local service smoke.
  - Evidence: focused attention/UI `190 passed`; touched-slices `137 passed`;
    full suite `965 passed, 2 skipped`; temp doctor exit 0; live smoke nine
    checks passed. Default `./WORKFLOW.md` doctor correctly exposed environment
    issues in this sandbox: `~/symphony_workspaces` not writable and no
    worktree-local `kanban/`.
  - _Requirements: 7.4, 7.5_
- [x] 8.2 Record decisions and rejected alternatives
  - Done: implementation decisions, rejected alternatives, and verification
    evidence recorded in the date changelog and delivery proof.
  - _Requirements: 7.5_
