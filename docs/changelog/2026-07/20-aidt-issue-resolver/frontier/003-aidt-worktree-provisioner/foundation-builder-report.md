# Frontier 003 foundation builder report

Date: 2026-07-21

Branch: `run/symphony-aidt-orchestrator-20260720`

Scope: contract and durable metadata foundation only

## Outcome

Implemented the closed, default-off AIDT worktree foundation without Git commands, provisioning, workspace/core
integration, live repositories, Jira, or network access.

The product now provides:

- strict `{enabled: bool}` activation and rejection of every generic hook/commit/merge/reuse seam;
- accepted issue-type normalization, exact `feat|fix` mapping, and backend/frontend A20 branch derivation;
- workflow-relative activation, manifest, ownership, attempt, common-Git-lock, and manifest-lock identities;
- bounded `AidtWorktreeFailure`, `AidtWorktreeResult`, sealed four-way `DelegateResult`, and exact
  `CompletionAuthorization` DTOs;
- exact manifest, route-scope, snapshot, proof, activation, ownership, and attempt-record schemas;
- canonical UTF-8 JSON with sorted keys, duplicate-key rejection, one newline, 128 KiB cap, exact scalar validation,
  and all four manifest states (`prepared -> ready -> removing -> removed`);
- revision CAS checks, 0600 exclusive same-directory temporary writes, file fsync, atomic replace, and classified
  directory fsync support;
- non-reversible POSIX advisory-lock identities, common-Git-before-manifest lock composition, and crash-release
  semantics;
- bounded registry discovery, corrupt/missing/case-collision failure, disabled/restart/tombstone guards, and stable
  workspace-path recognition;
- UTC whole-second attempt admission: manual/non-due backoff deny, due backoff durably consumes the next revision,
  ready admits resume without increment, post-intent failure becomes manual, and retry delay stays within 600 seconds.

The package facade keeps manifest persistence lazy until one of its exports is requested.

## TDD evidence

The first contract tracer failed because `symphony.aidt_worktree` did not exist, then passed after the minimal
default-off package seam. The expanded contract suite failed on missing DTOs before implementation. The manifest
suite then failed at collection because `manifest.py` did not exist, and its first implementation exposed five
concrete failures in state-shape and CAS handling before reaching green.

Final focused result: **55 passed, 0 failed**.

## Verification

| Gate | Result |
|---|---|
| `tests/test_aidt_worktree_contract.py` + `tests/test_aidt_worktree_manifest.py` | 55 passed |
| Existing `tests/test_aidt_routing_contract.py` compatibility | 99 passed |
| Ruff over the three product and two test files | all checks passed |
| Pyright over `src/symphony/aidt_worktree` | 0 errors, 0 warnings, 0 informations |
| Lazy facade fresh-process check | passed; `manifest` remained unloaded until requested |
| Product AST structure | 115 functions; max 34 lines; max nesting 3; no violations |
| Tracked whitespace | `git diff --check` clean |
| Five untracked no-index whitespace checks | no findings |

Tests use only temporary filesystem fixtures. No live Git command, remote, AIDT checkout, Jira call, or network call
was made.

## Owned files

- `src/symphony/aidt_worktree/__init__.py`
- `src/symphony/aidt_worktree/contract.py`
- `src/symphony/aidt_worktree/manifest.py`
- `tests/test_aidt_worktree_contract.py`
- `tests/test_aidt_worktree_manifest.py`
- this report

No commit was created. Other dirty-worktree files were left untouched.
