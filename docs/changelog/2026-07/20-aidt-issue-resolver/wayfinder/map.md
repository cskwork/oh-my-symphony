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
| 001-safe-jira-inbox | closed - correction reverified | none |
| 001a-returned-status-enforcement | closed - verified | none; corrects historical 001 |
| 002-aidt-routing-contract | closed | 001, 002a correction |
| 002a-routing-result-output-closure | closed | 002 implementation |
| 003-aidt-worktree-provisioner | closed - aggregate verification passed | none |
| 003a-route-child-dispatch-attestation | historical umbrella - verified | none |
| 003b-worktree-profile-identity-contract | historical umbrella - verified | none |
| 003c-durable-worktree-records | historical umbrella - verified | none |
| 003d-bounded-git-state-proofs | historical umbrella - verified | none |
| 003e-provisioner-lifecycle | historical umbrella - verified | none |
| 003f-process-runtime-ownership | historical umbrella - verified | none |
| 003g-core-workspace-integration | historical umbrella - verified | none |
| 003h-atomic-generation-publication | closed - verified | none |
| 003i-operator-profile-example | closed - verified | none |
| 003v-worktree-rollup-verification | closed - verified | none |
| 004-delivery-stage-enforcement | current - plan passed; 004a green, 004b-004v pending | none; 001, 002, 003 closed |
| 005-local-qa-adapters | pending | 004 |
| 006-dev-merge-promotion | pending | 004, 005 |
| 007-jenkins-dev-deploy-gate | pending | 006 |
| 008-dev-qa-and-completion | pending | 005, 007 |
| 009-managed-resolver-surfaces | pending | 004 |
| 010-integrated-fake-e2e | pending | 001-009 |
| 011-live-a20-dev-e2e | blocked | 010 and external gates |

## Frontier

Current frontier: 004-delivery-stage-enforcement. Frontiers 002 and 002a passed independent final verification:
177 isolated routing tests, 407 affected tests, 722 broad tests, 230 Frontier 001 preservation tests, and repository
parity of 1,656 passes/6 skips with only the accepted pre-change CI-1 failure.

Frontier 001 passed fresh returned-status reclosure verification through 001a. Frontier 002 added immutable production-base
routing, complete Jira source context, deterministic coordinator/children, and a fail-closed dispatch barrier; 002a
closed the public result-output boundary. Frontier 003 passed aggregate verification after bounded 003h/003i
corrections and 003v release verification. Live AIDT fetch, worktree creation, cleanup, and activation remain
forbidden.

Frontier 003 is a rollup, not a Build ticket. Tickets 003a-003g record the already executed deep-module seams as
historical umbrellas because their current single-module implementations cannot honestly satisfy the rough
five-file/500-net-line Build limit. They are commit-attribution and verification records, not new Build prompts.
The worker-sized 003h and 003i corrections and 003v aggregate verification passed. Frontier 004 is now the active
planning frontier; this status does not authorize merge, push, Jenkins, deployment, Jira mutation, or live AIDT use.
