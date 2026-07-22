# 001 - Safe assigned Jira inbox

Route: GREENFIELD

Status: closed - returned-status correction reverified

Blocked by: none

Unblocks: 002, 004, 009

Contains: 001a

## Goal

Periodically mirror only actionable A20 Jira issues assigned to the configured operator into the local
Symphony file board, including parent context when a subtask description is empty, without changing Jira.

## Acceptance criteria

- WHEN a Jira search runs THEN its JQL SHALL include project, configured actionable statuses, and the exact
  configured assignee/current-user constraint.
- WHEN an unassigned or differently assigned issue is returned by a faulty/mock server THEN import SHALL
  reject it fail-closed.
- WHEN an assigned subtask has no description THEN import SHALL include its parent summary and description.
- WHEN the same Jira issue is polled repeatedly THEN exactly one local card SHALL exist and source context
  SHALL update idempotently without overwriting local delivery evidence.
- WHEN Jira is unavailable or unauthorized THEN existing cards SHALL remain intact and the dashboard SHALL
  surface a retryable intake failure.
- The intake path SHALL perform no Jira comments, transitions, or other writes.

## Proof commands and surfaces

- Targeted red-green tests for Jira JQL, assignee validation, parent fetch, and idempotent mirror updates.
- Existing Jira tracker, file tracker, workflow, service, and web API tests.
- Fake-Jira integration test that polls twice and asserts one fully populated local card.
- Board/API issue detail and intake-failure health surface.

## Scope boundaries

- Owns Jira read/intake and local-card synchronization only.
- Does not create AIDT worktrees, implement stage prompts, merge, deploy, or perform dev QA.

## External blockers

- Fake-Jira proof is unblocked.
- Live polling is blocked by Atlassian authentication, canonical operator identity, actionable-status
  discovery, and parent-read permission. No Jira write is authorized.

## Historical closure evidence

- Nested proof vault: `frontier/001-safe-jira-inbox/`.
- Literal supergoal commit gate: PASS on 2026-07-20 after Build/Verify iteration 2.
- Frontier tests: 51 passed; affected regressions: 216 passed; Ruff, Pyright, and diff checks passed.
- Full repository suite retained only the accepted pre-change missing-`CI-1.md` failure.

## Reopened correction

Ticket 001a adds exact returned-status enforcement at the Jira response seam. Its bounded build evidence is recorded,
but Frontier 001 is not closed again until a fresh verifier repeats the focused, affected, static, and whitespace
gates and confirms zero board writes for an out-of-allowlist response.

## Reclosure

Fresh verification passed 5 focused and 235 affected cases, full static/whitespace gates, and aggregate repository
compatibility. Out-of-allowlist responses now fail before hydration/write; live Jira remains externally gated.
