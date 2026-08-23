# E2E refactor verification brief

- Target: current uncommitted working tree based on
  `1a5a7cece37032be333f4efb1050fb5561290793`.
- Comparison: functional.
- Goal: verify the phase-transition refactor through native browser and
  orchestrator E2E suites plus one real backend pipeline in a separate scratch
  project.
- Change boundary: read-only verification of product code. Test-owned scratch
  board/workspace/service state may be created and removed.
- Action cap: 100 browser interactions; CLI/test commands are recorded
  separately.
- Browser tool: `playwright-cli` for black-box live-app QA; the repository's
  Playwright pytest suite is also run as a native project gate.
- Safety: never dispatch the source checkout's `WORKFLOW.md`; use the existing
  isolated `%TEMP%\\opencode\\sym-e2e` project.

## Impact matrix

| Risk | Surface | Scenario | Expected evidence |
|---|---|---|---|
| must | direct | Real OpenCode worker advances a new ticket across all active phases to Done. | Ticket history, worker log, run detail, generated files/tests. |
| must | direct | Backend is rebuilt at each phase without losing the stable Run attempt/workspace. | `worker_phase_transition` events and one terminal run. |
| must | browser | Native web/chat/board browser E2E suite passes with browser flag enabled. | Pytest counts and browser artifacts on failure. |
| must | browser | Live admin UI loads the scratch board and exposes the E2E ticket/run without console-breaking errors. | `playwright-cli` snapshot, requests, screenshot, console summary. |
| must | adjacent | Lifecycle, deep-preset, contract, and release integration E2E suites pass. | Pytest counts and skips. |
| should | error | Scratch workflow passes `symphony doctor` before launch. | Doctor output/exit code. |
| should | API | Live `/api/v1/state`, issue detail, and run diagnostics agree with board/workspace state. | HTTP response summaries. |
| could | providers | Repeat the live pipeline with every installed provider CLI. | Covered only when authenticated, configured, and safe. |
| could | trackers | Repeat against Linear/Jira. | Covered only with configured test projects and credentials. |
