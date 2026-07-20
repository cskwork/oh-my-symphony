# GOAL - Frontier 002 AIDT Routing Contract

Turn Jira-managed coordinator cards into deterministic, revision-pinned AIDT service routes without guessing a
checkout or allowing an unrouted/stale card to dispatch.

## Success Criteria

- [x] Absent or disabled `aidt_routing` constructs no catalog/revision reader, changes no card, and preserves current
  candidate order and dispatch behavior.
- [x] The Jira source snapshot includes bounded structured summary/description/components/status/priority/issue type,
  URL/Jira-updated data and complete parent data, plus a deterministic content revision, without overwriting local
  board priority/state/URL/evidence; the body marker is display-only.
- [x] Enabled catalog validation is closed-schema and rejects Unicode/case collisions, duplicate resolved checkouts,
  absolute/traversal/symlink/TOCTOU escapes, unknown kinds, missing/disabled checkouts, unsafe Git metadata, and
  anything except regular marker/anchor blobs in an exact lowercase 40-hex local
  `refs/remotes/origin/aidt-prd` SHA-1 commit; staged/unstaged/untracked user state is preserved and ignored.
- [x] Git observation is hard-bounded and binary-exact, rejects untrusted Git metadata/object indirection, rechecks
  every repository identity and fixed ref inside the one whole-poll card lock, and never reads or changes the index,
  current branch, or working tree.
- [x] Routing requires confidence >=90, at least two independent authoritative categories, and component or verified
  code-symbol authority; keywords, parent text, dependencies, consumers, status, and priority cannot route alone.
- [x] A representative A20-1188 fixture routes only to `viewer-api` at 95 from `/v-api` plus the verified
  `MathAILearningCenterController` ownership chain; it creates no LMS child.
- [x] Below-threshold, tied, conflicting, unknown, or absent-checkout routes record bounded candidate evidence and
  recheck requirements, then move the coordinator to Human Review without a guessed service.
- [x] A single-service decision stores canonical service/kind/relative checkout, source/catalog/checkout revisions,
  branch prefix, confidence, bounded evidence, and supporting services while preserving local content.
- [x] A multi-service decision creates exactly one deterministic owned child per disjoint explicit change anchor,
  records coordinator-child links, rejects reparent/cross-coordinator ownership, and is idempotent across polls.
- [x] Source/catalog/checkout revision changes recompute routes without resetting child/local state, notes, plans, QA,
  or evidence; removed services never delete an existing child.
- [x] Batch preflight rejects unmanaged/case/symlink/duplicate child collisions and source drift before writes; an
  injected failure between child renames is reported as `partial_apply` and the next poll repairs owned artifacts
  without rollback/delete; equal decisions preserve bytes, mtime, decision timestamp, and `updated_at`.
- [x] A routing runtime/configuration/reload or required same-tick Jira-intake failure exposes only a sanitized
  retryable health category and stops before candidate fetch; successful routing filters every route-managed
  coordinator/stale/partial/retained child from dispatch until Frontier 003 consumes its pinned revision.
- [x] Public result, health, and logs expose only the frozen category enum and an allowed canonical card/service ref;
  malformed Git/Jira data, exceptions, output, source text, environment values, and paths never leak.
- [x] Routing is split by contract, immutable Git trust, pure decision, storage, and runtime; all changed/new functions
  are at most 50 lines with nesting at most four, and Ruff reports no compressed or inline-suite style violations.
- [x] Targeted routing tests, Frontier 001 tests, affected file/orchestrator/Jira regressions, Ruff, Pyright, diff check,
  and repository-wide regression parity pass.

## QA Cases

No browser case belongs to this frontier. Dashboard/TUI rendering of structured routes belongs to Frontier 009.

## Scope Boundary

No WORKFLOW activation, live Jira/AIDT mutation, `Issue` widening, worktree creation, worker prompt/stage gate, UI/TUI,
merge, Jenkins, or deployment change.
