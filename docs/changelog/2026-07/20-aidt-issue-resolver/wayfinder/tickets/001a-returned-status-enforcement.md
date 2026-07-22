# 001a - Returned Jira status enforcement

Route: CORRECTION / VERIFICATION

Status: closed - verified

Blocked by: none; corrects historical 001 implementation

Unblocks: 001 reclosure, 003v, 004, 009

## Goal and interface contract

Own the Jira-response status seam: after bounded status parsing, accept a returned issue only when its status name is
an exact member of the configured actionable `active_states`. Request JQL is nomination; the returned row must still
prove it belongs to that scope before any file-board mutation.

## Bounded file ownership

- `src/symphony/trackers/jira.py`
- `tests/test_jira_intake.py`

The completed correction is bounded to two files and remains below the rough five-file/500-net-line Build guide.

## Acceptance criteria

- A returned status outside `active_states` rejects the complete intake batch with sanitized `invalid_response` and
  writes no local card.
- Membership is exact, case-sensitive, and whitespace-sensitive; configured `Ready` rejects `ready`, `Ready `, and
  ` Ready` while accepting exact `Ready`.
- The configured allowlist still reaches the actual client/JQL, and assignment/project/pagination/parent/source,
  GET-only transport, disabled mode, and existing-card preservation remain unchanged.

## Recorded build proof and fresh verification

- Recorded build commands/results are in `/private/tmp/f003-jira-status-result.md`: focused 5 cases, complete intake
  and Jira tracker suites, affected intake/file-board/orchestrator/service/web suites, Ruff, Pyright, and whitespace.
- Fresh verification passed the focused 5-case regression, 235-case affected matrix, production static checks,
  fixed-base/tracked/all-untracked whitespace, and aggregate repository compatibility.

## Scope boundary

Does not change configured statuses, normalize returned text, call live Jira, write Jira, alter routing/worktrees, or
update GOAL/run-state/Z/commit state.
