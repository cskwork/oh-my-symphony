# 002 - AIDT routing contract

Route: GREENFIELD

Status: drafting

Blocked by: 001

Unblocks: 003, 004

## Goal

Resolve each mirrored issue to validated AIDT service ownership from component, domain, parent, and code
evidence, or stop for human review; never choose a service from an unsupported keyword guess.

## Acceptance criteria

- The service catalog validates service name and checkout path before a route is frozen.
- The card records service, service directory, route evidence, issue type, branch prefix, source revision,
  and confidence.
- Component/domain evidence is checked against current context/code ownership; keywords are supporting evidence
  only, and there is no default to aidt-lms-api.
- A multi-service issue creates one coordinator and one independently verifiable child per repository.
- Missing, conflicting, absent-checkout, or below-90-percent evidence moves to Human Review/Blocked.

## Proof commands and surfaces

- pytest -q tests/test_aidt_routing.py
- Fixture catalog cases for single-service, ambiguous, absent-service, parent-only, and multi-service cards.
- Board/API detail showing route evidence, child links, confidence, and blocking reason.

## Scope boundaries

- Owns route resolution and local-card decomposition only.
- Does not create worktrees, approve plans, edit product code, merge, deploy, or write Jira.

## External blockers

- Fixture proof is unblocked.
- Ambiguous live routes require operator approval; missing component/parent context remains fail-closed.
