# 002a - Routing result output closure

Route: CORRECTION

Status: closed

Blocked by: Frontier 002 implementation state

Unblocks: Frontier 002 final verification, then 003

## Goal

Make every public routing result fail closed at construction so malformed booleans, counts, identifiers, categories,
or refs cannot reach repr, logs, health, or dispatch decisions.

## Acceptance criteria

- Valid routing results remain byte-for-byte/field-for-field unchanged.
- Every malformed public field normalizes the whole result to one bounded `internal_error` failure with dispatch off.
- Blocked IDs accept only canonical Jira coordinator or deterministic route-child IDs within the frozen batch cap.
- Repr, structured logs, and health expose no injected source, payload, path, exception, or object repr.
- Import permutations, five routing suites, affected/full parity, static gates, and doctor retain accepted baselines.

## Scope boundaries

Owns only the public result normalization boundary and its contract/runtime-output tests. It does not alter routing,
Git observation, scoring, storage, Jira intake, worktrees, activation, merge, deployment, or live data.
