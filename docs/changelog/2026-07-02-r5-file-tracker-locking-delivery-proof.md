# Delivery Proof

## Eval Intent

- Goal: implement R5 file-board write locking from the reliability handoff.
- Constraints: preserve Markdown ticket format, keep file tracker synchronous,
  avoid mutating ticket files on pure reads, and keep Windows as a documented
  fallback instead of adding a new dependency.
- Tradeoffs: POSIX uses advisory `fcntl.flock`; non-POSIX falls back to the
  previous behavior until a platform-specific lock is added.

## Before State

- Mode: LEGACY
- Proof: `parse_ticket_file` persisted auto-healed frontmatter during read;
  generated IDs were selected by `next_identifier()` before a separate
  `create()` call; ticket read-modify-write paths parsed and overwrote without
  a shared lock.
- Command or artifact: `docs/plans/2026-07-02-reliability-handoff.md`.
- What this proves: concurrent creates could choose the same generated ID and
  concurrent ticket updates could lose one writer's body or frontmatter change.

## After Target

- Expected behavior: pure reads never rewrite a ticket; generated ID allocation
  and creation happen inside one allocator lock; every file-tracker
  read-modify-write mutation is serialized by a per-ticket lock; if `updated_at`
  or file mtime changes between read and write, the mutation is re-applied to
  the fresh file.
- Compatibility to preserve: existing `create(identifier=...)`,
  `transition`, `update_fields`, `record_agent_kind`, `append_note`, web issue
  creation, and TUI issue creation behavior.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| read-autoheal-red | `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_parse_ticket_file_auto_heals_markdown_inside_front_matter` | evaluator_owned | Read auto-heal used to rewrite ticket files | before |
| allocator-red | `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_create_with_next_identifier_is_unique_under_concurrent_calls` | evaluator_owned | Generated create needed an atomic public API | before |
| rmw-red | `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_append_note_preserves_concurrent_writes` | evaluator_owned | Concurrent append_note lost one writer without locking | before |
| file-tracker | `.venv/bin/python -m pytest -q tests/test_tracker_file.py` | frozen_repo | File tracker parser, create, mutation, lock, and CAS behavior | after |
| webapi-file-create | `.venv/bin/python -m pytest -q tests/test_tracker_file.py tests/test_webapi.py` | frozen_repo | File tracker plus web generated-create behavior | after |
| compile-check | `.venv/bin/python -m compileall -q src/symphony/trackers/file.py src/symphony/webapi.py src/symphony/tui/app.py` | frozen_repo | Edited Python files parse | after |
| full-tests | `.venv/bin/python -m pytest -q` | frozen_repo | Broad Python regression check | after |
| diff-check | `git diff --check` | frozen_repo | No whitespace errors in edited files | after |
| workflow-doctor | `.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Current workflow validates in the operator environment | after |
| browser-e2e | `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` | frozen_repo | Browser UI flow can execute when Playwright Chromium is installed | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | Auto-heal persisted during `parse_ticket_file`, making reads observable writes. | Return healed front/body without writing; mutation paths own persistence. | read-autoheal-red, file-tracker |
| d2 | auto-fix | resolved | Locking `next_identifier()` alone leaves a race before the later `create()`. | Added `create_with_next_identifier()` and moved web/TUI generated creates to it. | allocator-red, webapi-file-create |
| d3 | auto-fix | resolved | Ticket mutation methods parsed and overwrote independently. | Added per-ticket lockfile plus shared mutation helper for transition/update/note/agent-kind/warning-strip. | rmw-red, file-tracker |
| d4 | auto-fix | resolved | Non-cooperating writers cannot share the in-process lock, and `updated_at` can stay equal within one second. | Re-read before write and re-apply when `(updated_at, mtime_ns)` moved. | file-tracker |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| read-autoheal-red | pass | Failed before implementation when the source file was rewritten. | The test caught the write-on-read contract violation. | Other mutation paths |
| allocator-red | pass | Failed before implementation because `create_with_next_identifier` did not exist. | The new public generated-create contract was absent before the fix. | Cross-process contention |
| rmw-red | pass | Failed before locking with final body containing only one note. | The old read-modify-write path could lose concurrent note appends. | All possible external editor races |
| file-tracker | pass | `35 passed`. | Parser, generated create, per-ticket lock, CAS reapply, G5 strip, and existing file tracker behavior. | Web/TUI integration beyond file tracker |
| webapi-file-create | pass | `54 passed in 0.70s`. | Web issue create still generates `TASK-n`; file tracker tests still pass together. | Browser UI interaction |
| compile-check | pass | No output, exit 0. | Edited Python files compile. | Runtime workflow behavior |
| full-tests | pass | `926 passed, 2 skipped, 1 warning in 59.23s`. | Broad repository regression status after R5. | Real doctor or browser runtime |
| diff-check | pass | No output, exit 0. | Edited files have no whitespace errors. | Runtime behavior |
| workflow-doctor | blocked | Port `9999` is occupied and `/Users/danny/symphony_workspaces` is not writable from the sandbox. | Current environment blocks the final doctor gate. | Real unsandboxed doctor status |
| browser-e2e | blocked | `1 skipped`; Playwright Chromium executable missing from `/Users/danny/Library/Caches/ms-playwright/...`. | The opt-in E2E test is wired and skips with the documented missing dependency. | Actual browser interaction |

## Residual Risk

- Not proven: Windows locking behavior, non-cooperating external writers that
  change a ticket after the final CAS token read but before `os.replace`,
  unsandboxed doctor status, and browser/TUI end-to-end generated-create
  interaction with an installed Chromium binary.
