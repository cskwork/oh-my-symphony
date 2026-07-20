# 011 - Live A20 dev end-to-end

Route: GREENFIELD

Status: blocked

Blocked by: 010 and external gates

Unblocks: root GOAL completion

## Goal

Exercise one explicitly approved non-production A20 issue from assigned-only intake through isolated
implementation, aidt-dev deployment, final dev QA, and shared dashboard/TUI evidence.

## Acceptance criteria

- Authenticated read-only intake proves exact assignment, approved status, parent context, idempotency, and no write.
- The operator approves issue revision, route, plan hash, scope, and each Git/Jenkins/dev-data/Jira mutation.
- One service worktree from origin/aidt-prd passes Build, Review, Local QA, commit, temporary aidt-dev promotion,
  and remote-SHA proof.
- Before unattended deployment, the known committed DEV credential is revoked/rotated and removed; proof checks
  only remediation/absence and never reads or reproduces its value.
- The exact reviewed Jenkins job triggers once, deploys the authorized SHA, and correlates to its run.
- Final dev health/behavior pass against that run/SHA with side-effect evidence and sanitized artifacts.
- Dashboard, API, and TUI agree; Jira stays unchanged unless sanitized write-back is separately approved.

## Proof commands and surfaces

- Credential-status checks that do not print material; read-only Jira identity/search and Jenkins discovery.
- Resolver doctor, health, state, board, history, and logs on 127.0.0.1:9918.
- Service tests/local QA, Git SHA proof, reviewed Jenkins run, and issue-specific dev API/browser/protocol proof.

## Scope boundaries

- One explicitly approved A20 issue and aidt-dev only.
- No production/staging, broad polling, force push, blind retry, unapproved mutation, credential inspection,
  or automatic Jira write-back.

## External blockers

- Atlassian auth, canonical account ID, actionable statuses, and parent-read permission.
- Jenkins credential remediation, auth, job/parameter mapping, permission, and deployed-SHA signal.
- QA identity, dev URLs, workload/health map, safe write fixture where needed, and rollback policy.
- Explicit plan, Git push, Jenkins trigger, dev-data mutation, and optional Jira write-back approvals.
