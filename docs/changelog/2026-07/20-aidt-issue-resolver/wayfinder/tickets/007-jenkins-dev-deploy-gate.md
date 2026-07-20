# 007 - Jenkins dev deploy gate

Route: GREENFIELD

Status: pending

Blocked by: 006

Unblocks: 008, 010, 011

## Goal

Deploy exactly one reviewed aidt-dev merge SHA through a secret-free, discovered Jenkins job mapping and
an idempotent single-trigger state machine.

## Acceptance criteria

- Job, context, parameters, permissions, and deployed-SHA signal come from reviewed discovery/configuration,
  never a guessed pattern or conflicting legacy table.
- Preflight verifies dev context, exact merge SHA, parameters, queue/latest run, and trigger authorization.
- One trigger intent produces at most one Jenkins run across timeout, crash, and restart.
- Timeout becomes Unknown; correlation by job, parameters, time, and run number occurs before any retry.
- Jenkins SUCCESS passes only when the correlated deployed SHA equals the authorized SHA.
- Logs/evidence are bounded and redacted; no credential material enters cards, commands, logs, or Git.
- Unattended deployment stays disabled until the known committed DEV credential is revoked/rotated and removed.

## Proof commands and surfaces

- pytest -q tests/test_aidt_jenkins_gate.py
- Fake-Jenkins cases for one trigger, timeout correlation, restart, duplicate suppression, failure, wrong SHA,
  and redaction.
- Reviewed read-only jk context/job/parameter/run metadata when Jenkins access is approved.

## Scope boundaries

- Owns job mapping, trigger idempotency, run correlation, result, and deployed-SHA proof.
- Does not guess jobs, expose credentials, retry blindly, merge code, or declare completion.

## External blockers

- The known committed DEV credential must be revoked/rotated and removed before unattended deployment;
  its value must not be inspected or reproduced.
- Jenkins auth, job/parameter mapping, permission, and deployed-SHA signal remain unverified.
- A live trigger requires separate explicit authorization.
