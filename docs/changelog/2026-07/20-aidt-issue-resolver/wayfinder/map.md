# AIDT Jira Issue Resolver Frontier Map

## Destination

An always-running Symphony service on a dedicated local port imports only the operator's actionable A20
Jira issues, resolves each through isolated Codex worktrees and evidence gates, deploys verified AIDT changes
to aidt-dev Jenkins, and records final dev E2E evidence in dashboard/TUI-visible history.

## Current state evidence

- Root GOAL.md, PLAN.md, and QA.md in this run vault.
- WORKFLOW.md, Jira/file trackers, managed service, board/API/UI/TUI, registry, and runtime tests.
- Authenticated UI observations for A20-1188 and parent A20-1186 on 2026-07-20.
- Exploration reports covering AIDT routing, worktrees, gates, QA, promotion, Jenkins, runtime, and E2E.

## Decisions

- Jira is a read-only assigned-issue inbox; the local file board owns delivery stages and evidence.
- Use the built-in dashboard on 127.0.0.1:9918 with no legacy viewer and an API-backed TUI attach mode.
- Start service changes from recorded origin/aidt-prd SHAs; promote through temporary origin/aidt-dev merge
  worktrees without occupying protected branches in main service checkouts.
- Disable generic auto-merge. Code validators, not prompts, authorize transitions.
- Keep secrets in environment variables/external stores. Future issue plans require human approval by default.
- Jira write-back is disabled unless a separate sanitized write is explicitly approved.

## External gates

- Atlassian authentication, canonical account ID, actionable-status allowlist, and parent/comment permissions.
- A known committed DEV Jenkins credential in local guidance must be revoked or rotated and removed before
  unattended deployment. Its value must not be inspected, copied, logged, or reproduced.
- Exact Jenkins jobs, parameters, permissions, queue correlation, and deployed-SHA signals need reviewed discovery.
- Service boot/health recipes, dev URLs, workload mappings, QA identities, safe write fixtures, and rollback policy.

## Out of scope

- Production or staging deployment; other assignees; hardcoded credentials or identities.
- Automatic approval of future issue plans, Git pushes, Jenkins runs, dev-data mutation, or Jira writes.
- Generic all-service QA, broad UI rewrites, or cleanup of user-owned AIDT state.

## Ticket graph

| Ticket | Status | Blocked by |
|---|---|---|
| 001-safe-jira-inbox | closed | none |
| 002-aidt-routing-contract | drafting | 001 |
| 003-aidt-worktree-provisioner | pending | 002 |
| 004-delivery-stage-enforcement | pending | 001, 002, 003 |
| 005-local-qa-adapters | pending | 003, 004 |
| 006-dev-merge-promotion | pending | 004, 005 |
| 007-jenkins-dev-deploy-gate | pending | 006 |
| 008-dev-qa-and-completion | pending | 005, 007 |
| 009-managed-resolver-surfaces | pending | 001, 004 |
| 010-integrated-fake-e2e | pending | 001-009 |
| 011-live-a20-dev-e2e | blocked | 010 and external gates |

## Frontier

Current frontier: 002-aidt-routing-contract only.

Frontier 001 passed its nested literal commit gate after two iterations: 51 intake tests and 216 affected
regressions passed, with the sole repository-wide failure unchanged from baseline. Freeze fail-closed AIDT service
routing before any service worktree, implementation, merge, deployment, managed runtime, or live E2E work begins.
