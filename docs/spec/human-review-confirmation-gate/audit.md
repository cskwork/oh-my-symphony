# Audit: Human Review Confirmation Gate

Date: 2026-07-03.

## Finding Summary

| Area | Status | Evidence |
|---|---|---|
| Prompt contract | Updated | Learn sets `Done` for normal success; `Human Review` is reserved for critical/manual intervention. |
| TUI confirmation | Present | `KanbanApp.action_confirm_done_focused` moves focused Human Review cards to Done. |
| Standalone board viewer confirmation | Present | `tools/board-viewer` renders `Confirm Done` and posts `/api/kanban/{id}/confirm-done`. |
| Service web board confirmation | Missing | `src/symphony/web/static/app.js` has `Skip Learn` but no `Confirm Done` or confirm API client method. |
| Service web API confirmation | Missing | `src/symphony/webapi.py` has issue CRUD and `skip-learn`, but no `/api/v1/issues/{id}/confirm-done` route. |
| Evidence contract wiki | Missing in host | RERUN-204 workspace has `docs/llm-wiki/verify-evidence-contract.md`; host repo does not. |

## Checked Files

- `WORKFLOW.md`
- `docs/symphony-prompts/file/base.md`
- `docs/symphony-prompts/file/stages/learn.md`
- `src/symphony/tui/app.py`
- `tools/board-viewer/src/js/ticket.js`
- `tools/board-viewer/src/js/board.js`
- `tools/board-viewer/src/js/api.js`
- `tools/board-viewer/server.py`
- `src/symphony/web/static/app.js`
- `src/symphony/webapi.py`
- `tests/test_board_viewer.py`
- `tests/test_web_static_contract.py`
- `tests/test_webapi.py`
- `/private/tmp/symphony-e2e-todo-rerun-OfAuBV/workspaces/RERUN-204/docs/llm-wiki/verify-evidence-contract.md`

## Current Proof

### Prompt Contract

The file prompt now declares this flow:

```text
Todo -> In Progress -> Verify -> Learn -> Done
```

Learn stage requirements say to append `## As-Is -> To-Be Report` and set
state to `Done` for normal success. The base prompt says `Human Review` is only
for real critical/manual intervention.

### Existing Non-Service Confirm Paths

TUI:

- `action_confirm_done_focused` checks the focused card state.
- It refuses non-Human Review cards.
- It calls the tracker state update to `Done`.

Standalone board viewer:

- `ticket.js` renders `Confirm Done` when
  `normalizeState(ticket.state) === "human review"`.
- `api.js` posts `/api/kanban/${id}/confirm-done`.
- `server.py` moves only Human Review tickets to Done.
- `tests/test_board_viewer.py` covers rendering, success, and wrong-state
  refusal.

### Missing Service Board Path

Service web board:

- `src/symphony/web/static/app.js` defines `api.skipLearn` but no
  `api.confirmDone`.
- `buildCardEl` renders `Skip Learn` for Learn cards, but no Human Review
  action.
- `buildTerminalSectionEl` renders Human Review cards through `buildCardEl`,
  so adding the action there covers both active terminal section and
  all-columns mode.

Service web API:

- `src/symphony/webapi.py` registers issue create/detail/patch/delete and
  `skip-learn`.
- There is no narrow confirm route.
- The generic patch route can set state, but it does not express final human
  confirmation and is not an adequate operator affordance.

### Evidence Contract Drift

The RERUN-204 workspace contains the missing wiki page. Its rule is:

- Verify evidence cells are checked relative to the ticket docs root.
- `## Security Audit` and `## AC Scorecard` cells should cite
  `qa/qa.log`, `qa/details.md`, `work/harness/run.js`, or `n/a`, not
  repo-root paths like `docs/RERUN-204/qa/qa.log`.
- Source anchors belong in details artefacts.

The host repo currently lacks that page and its `INDEX.md` row, so future
operators do not have the recovered rule available from the main checkout.

## Doctor Result

`symphony doctor ./WORKFLOW.md` was run during this audit.

Result:

- PASS: port 9999 free.
- PASS: shell, configured Claude agent, prompt files, customized
  `after_create`, tracker board root, and standalone viewer script.
- FAIL: `/Users/danny/symphony_workspaces` not writable in this sandbox
  (`Operation not permitted`).

Interpretation: the workflow shape is readable and relevant checks pass; the
workspace-root failure is environment permission, not the Human Review confirm
gap.

## Pre-Existing Dirty Files

Before this spec was written, these files were already modified:

- `docs/changelog/changelog-2026-07-03.md`
- `tests/test_orchestrator_dispatch.py`

This spec intentionally does not touch `tests/test_orchestrator_dispatch.py`.
