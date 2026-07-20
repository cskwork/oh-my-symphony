# 008 - Dev QA and completion

Route: GREENFIELD

Status: pending

Blocked by: 005, 007

Unblocks: 010, 011

## Goal

Authorize completion only after service-specific dev health and issue behavior are proven against the
correlated Jenkins run and deployed SHA, with side effects and recovery explicit.

## Acceptance criteria

- Dev QA starts only after correlated Jenkins success and deployed-SHA confirmation.
- The adapter proves workload/health, issue-specific behavior, and the smallest relevant regression set;
  HTTP status alone is insufficient.
- LMS/viewer web proof captures console/page/API/network failures and retains failure artifacts.
- Timeout, skipped check, missing account/dependency, warning, unsafe mutation, or SHA mismatch cannot reach Done.
- Completion binds the acceptance scorecard, Jenkins run, deployed SHA, freshness, and side-effect result.
- Jira write-back defaults to none; sanitized comment/transition is separately human-approved.
- Failed dev QA records a recovery/rollback decision rather than silently retrying or redeploying.

## Proof commands and surfaces

- pytest -q tests/test_aidt_dev_qa_completion.py
- Fixture health/API/browser runs for pass, failure, wrong SHA, unsafe mutation, restart, and redaction.
- Board/API Dev QA history plus retained sanitized browser/runtime artifacts.

## Scope boundaries

- Owns dev verification, completion authorization, failure/recovery state, and write-back gating.
- Does not define Jenkins jobs, auto-redeploy, mutate dev without approval, or enable production/staging.

## External blockers

- Dev URLs, workloads, health endpoints, QA identities, and safe disposable fixtures are incomplete.
- Rollback and Jira comment/transition policies require operator decisions.
- Until approved, dev mutation and Jira write-back remain disabled.
