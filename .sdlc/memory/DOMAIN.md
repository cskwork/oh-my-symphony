# Domain knowledge — how THIS system works (≤100 lines; over → split by

# subdomain into memory/domain/<area>.md and keep one pointer line here)

# Continuously updated: researchers write back verified facts; ship retro

# harvests new entries. Facts carry [verified: how] like intent claims

## Terms (ubiquitous language)

- blocker: an incoming issue relation whose type is `blocks`.

## Facts

- The Linear tracker reads blockers from `Issue.inverseRelations` and filters non-blocking relation types during normalization. [verified: `src/symphony/trackers/linear.py:195-207`]
- Candidate polling and full issue validation use separate GraphQL documents. [verified: `src/symphony/trackers/linear.py:33-130`]
- Linear connections default to 50 records and expose more records through `pageInfo.hasNextPage`. [verified: https://linear.app/developers/pagination]
- Candidate polling and full issue fetch request `inverseRelations(first: 50)` and reject an incomplete nested connection. [verified: `src/symphony/trackers/linear.py:56-62,121-127,178-193`; focused tests]

## Constraints (load-bearing)

- A Linear query change must preserve every blocker returned by Linear. [verified: scheduler depends on normalized `blocked_by`]
- Normal CI must not require tracker credentials. [verified: user decision, 2026-08-29]
- A partial blocker relation connection must fail closed before dispatch or stage validation. [verified: `tests/test_tracker_linear_full.py`, `tests/test_orchestrator_dispatch.py`, and `tests/test_orchestrator_contract_integration.py`]
