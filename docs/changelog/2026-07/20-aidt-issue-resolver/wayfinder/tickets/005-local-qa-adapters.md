# 005 - Local QA adapters

Route: GREENFIELD

Status: pending

Blocked by: 004

Unblocks: 006, 008, 010

## Goal

Provide explicit LMS/viewer backend and web local-QA recipes that prove changed behavior and side effects,
with unsupported environments stopping visibly instead of passing.

## Acceptance criteria

- Backend adapters declare focused/full tests, build, supported boot/health, authenticated behavior/data
  assertions, and error-log review.
- Web adapters declare typecheck/unit/build plus Playwright behavior, console/page/API/network, persistence,
  and side-effect checks.
- MyBatis proof uses read-only query/EXPLAIN and returned-data validation; shared-environment DML is forbidden.
- Missing boot, browser, account, database, or functional proof produces Environment Block/Not proven.
- Evidence is bound to the source SHA and contains no identity, token, cookie, credential, or secret.

## Proof commands and surfaces

- pytest -q tests/test_aidt_local_qa_adapters.py
- LMS/viewer backend/web fixtures for success, failure, timeout, missing dependency, and redaction.
- Retained test/log/Playwright artifacts and Local QA evidence bound to source SHA.

## Scope boundaries

- First slice covers LMS/viewer backend and web only; other service families require child tickets.
- Does not merge, deploy, mutate shared dev data, or substitute compilation/HTTP status for behavior proof.

## External blockers

- Service boot/health recipes and safe database/browser identities are incomplete.
- Live QA waits for environment-provided identities/endpoints; fixture tests remain runnable.
