# Design: Human Review Confirmation Gate

## Overview

The fix is a narrow parity and contract hardening change.

The service web board needs the same final action that already exists in the
TUI and standalone board viewer for intervention cards: Human Review cards get
an explicit `Confirm Done` control. That control calls a Human Review-only API
route. The route moves the ticket to Done, records stats, and refreshes the
orchestrator snapshot.

The Verify evidence rule is documented and pinned with tests at the same time
because RERUN-204 showed that a run can reach the final handoff only after a
contract rewind caused by path-coordinate drift.

## Current State

Verified current behavior:

- `docs/symphony-prompts/file/stages/learn.md` requires Learn to append
  `## As-Is -> To-Be Report` and set state to `Done` for normal success.
- `docs/symphony-prompts/file/base.md` says `Human Review` is only for real
  critical/manual intervention, not normal completion.
- TUI has `action_confirm_done_focused`.
- Standalone `tools/board-viewer` has `Confirm Done`,
  `/api/kanban/{id}/confirm-done`, and tests.
- Service web board has `Skip Learn` but no `Confirm Done` string, API client
  method, or `/api/v1/issues/{id}/confirm-done` route.
- The host repo does not currently have
  `docs/llm-wiki/verify-evidence-contract.md`, though the RERUN-204 workspace
  produced that page.

## Architecture

```text
Human Review card
  -> service web board button
  -> api.confirmDone(identifier)
  -> POST /api/v1/issues/{identifier}/confirm-done
  -> FileBoardTracker transition Human Review -> Done
  -> stats transition + orchestrator refresh
  -> next board poll shows Done
```

The service board stays framework-free and uses the existing DOM helper
pattern in `src/symphony/web/static/app.js`.

The service API keeps mutations under `src/symphony/webapi.py` beside the
existing issue routes. The route uses the same file tracker and stats store as
`PATCH /api/v1/issues/{identifier}`, but it validates the source state before
writing.

## Components

### Web API

Add `handle_issue_confirm_done` beside `handle_issue_skip_learn`.

Responsibilities:

- Validate identifier with `_check_identifier`.
- Load the file tracker through `ctx.file_tracker()`.
- Fetch current issue by identifier.
- Return 404 if the ticket does not exist.
- Return 409 if `current.state.lower() != "human review"`.
- Transition the ticket to the canonical `Done` state from workflow config.
- Record stats from `human review` to `done`.
- Call `orchestrator.request_refresh()`.
- Return `{identifier, confirmed: true, state: "Done"}`.

State lookup should use `_valid_states(cfg)` so custom casing still works. If
the workflow has no Done terminal state, return 409 with an actionable message;
this spec does not invent a replacement terminal state.

### Web Static App

Add an API client method:

```js
confirmDone: (id) => apiRequest(`/issues/${encodeURIComponent(id)}/confirm-done`, { method: 'POST' })
```

Add a card action branch in `buildCardEl`:

- shown when `!readOnly`
- shown when `issue.state` normalizes to `human review`
- hidden for all other states
- click handler stops propagation so it does not open the drawer
- calls `runControlAction(api.confirmDone, issue.identifier, 'Confirmed Done')`

The existing terminal section already reuses `buildCardEl`, so active-scope
Human Review cards get the action without a second rendering path.

### Evidence Contract Wiki

Bring the RERUN-204 rule into the host repo as:

- `docs/llm-wiki/verify-evidence-contract.md`
- an `INDEX.md` row with slug `verify-evidence-contract`

The page records the docs-root-relative rule:

- evidence cells in `## Security Audit` and `## AC Scorecard` cite paths
  relative to `docs/<ticket>/`
- source proof belongs in details artefacts such as `qa/details.md`
- table cells should stay to one path or `n/a`

### Tests

Default tests:

- `tests/test_web_static_contract.py` asserts `Confirm Done`,
  `api.confirmDone`, `confirm-done`, and the Human Review state guard in
  `src/symphony/web/static/app.js`.
- `tests/test_webapi.py` covers successful Human Review -> Done confirmation,
  wrong-state 409, and missing-ticket 404.
- Contract tests cover docs-root-relative evidence cells and reject repo-root
  paths in the Verify table context.

Browser-gated tests:

- Extend `tests/test_web_browser_e2e.py` so a seeded Human Review card exposes
  `Confirm Done`, clicking it moves the card to Done, and no console errors are
  emitted.

## Error Handling

| Scenario | Response |
|---|---|
| Unknown ticket | 404 `issue_not_found` |
| Non-file tracker | Existing `board editing requires tracker.kind: file` error |
| Ticket not in Human Review | 409 `confirm_rejected` |
| Workflow has no Done state | 409 `confirm_rejected` |
| Tracker write failure | Existing wrapped 500 logging path |
| Browser action fails | Card remains in place; toast shows API message |

## Alternatives Considered

### Make Human Review the normal final hop

Rejected. Human Review is now an intervention-only terminal state. Normal
successful work should close as Done after Verify, Learn, final report, and
history proof all pass.

### Rely on drag/drop or generic PATCH

Rejected as the primary UX. It is easy to miss, does not communicate the
human-confirmation meaning, and can look like a generic state edit. Generic
PATCH remains for compatibility, but the service board needs a first-class
confirm action.

### Fix only the standalone board viewer

Rejected. That surface already has the action. The live service board is the
missing path.

### Only document the evidence rule

Rejected. Documentation without a contract regression test allows the same
path-coordinate mismatch to return.

## Rollout

1. Land the service-board API and UI tests first; they should fail before the
   implementation.
2. Add the minimal API and UI implementation.
3. Import the evidence contract wiki page and add its index row.
4. Add or tighten contract tests for docs-root evidence cells.
5. Run focused tests, browser E2E when available, `symphony doctor`, and
   whitespace checks.
